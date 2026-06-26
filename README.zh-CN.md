# MyGameStory

English documentation: [README.md](README.md)

`MyGameStory` 是一个本地 Steam 游戏库数据项目。它用简单、可审计、可 git diff 的文件保存源数据，并自动生成适合 Excel 浏览和后续分析的 CSV 视图。不使用复杂 SQL 数据库。

分类确认和后续导入格式见：[docs/classification_workflow.zh-CN.md](docs/classification_workflow.zh-CN.md)

## 数据模型

- `data/manual/*.jsonl`：人工维护的源数据，包括游戏身份、分类、别名、已确认名称解析、个人评价和购买记录。
- `data/imports/`：Steam API、截图、OCR、本地集合导出的原始导入文件。原始导入只追加保存，不覆盖。
- `data/snapshots/*.jsonl`：从原始导入解析出的历史快照，例如库存、游玩时长、成就和安装状态。
- `data/suggestions/*.jsonl`：机器、OCR、导入流程产生的待审核建议。
- `data/derived/*.csv`：自动生成的宽表和统计表。不要手工编辑。

JSONL 文件里不写注释。字段说明、流程和注意事项放在 README 和脚本里。

## 主键设计

Steam 游戏使用 `game_id = "steam:<appid>"`，例如 `steam:292030`。中文名、英文名、Steam 商店名、OCR 文本都只是标题或别名，不能作为主键。

分类主键使用稳定英文 slug，例如 `role_playing`。`B.03` 这类 legacy code 和中文显示名只是展示层信息。

## A/B 分类和其他数据

- B 类写入 `primary_category`，表示主玩法类别。每个游戏最多一个 B 类，正式值必须使用稳定 slug，例如 `narrative_adventure`，不要用 `B.04` 当数据主键。
- A 类分成两层：`curation_state` 是互斥的个人库管理状态，每个游戏最多一个；`special_flags` 是可叠加标记，可以有多个。
- 当前 `curation_state`：`active_play`、`unstarted`、`handpicked`、`sampled_continue`、`card_stock`。
- 当前 `special_flags`：`multiplayer_candidate`、`completion_target`。
- `favorite=true` 是收藏状态，当前规则要求 `curation_state == "handpicked"`。
- `installed` 是动态安装状态，来自本地扫描或 Steam 状态，不等于 A 类。
- 评分、评价、进度写入 `data/manual/personal_reviews.jsonl`；购买价格写入 `data/manual/purchases.jsonl`；这些都用 `game_id` 关联。
- 其他平台后续也用同样模型，`game_id` 形如 `epic:<id>`、`gog:<id>`、`xbox:<id>`，平台定义放在 `config/platforms.json`。

## 一键更新 Steam 库

新增购买游戏后的日常入口是交互式更新脚本：

```powershell
python scripts/update_steam_library.py
```

它会自动重新导出当前 Steam 库，生成导入 preview，打印本次新增游戏，逐个提示输入 B 类分类，确认后应用 preview，重建 CSV 视图，并运行校验。

分类提示里可以输入数字 `1`-`17`、legacy code 例如 `B.04`，或稳定英文 slug 例如 `narrative_adventure`。直接回车表示先保留为 `pending`，以后再补分类；输入 `?` 可以重新显示类别列表；输入 `q` 会把剩余新增游戏全部先标记为 pending。

常用选项：

```powershell
python scripts/update_steam_library.py --preview-only
python scripts/update_steam_library.py --skip-classification-prompt
python scripts/update_steam_library.py --yes
```

## Steam 库导入

先导出 Steam owned-library 数据。凭据来自环境变量 `STEAM_API_KEY`、`STEAM_ID`，或本地忽略文件 `scripts/steam_api.local.json`。

```powershell
python scripts/export_steam_owned.py
```

然后从 CSV 生成导入 preview：

```powershell
python scripts/import_steam_owned.py --input data\imports\steam_owned\steam_library_raw.csv --timestamp 20260625T203000+0800
```

检查 preview 后再应用。这个 owned-library 导入器只写入 `data/manual/games.jsonl` 和 `data/snapshots/steam_owned_snapshot.jsonl`，不会写分类。

```powershell
python scripts/import_steam_owned.py --apply-preview data\imports\steam_owned\20260625T203000+0800.import_preview.json
```

每轮只处理一个来源。Steam owned-library 导入只处理库存身份和库存快照。

## OCR / 文本分类导入

截图 OCR 或手动 OCR 文本先生成 preview。OCR 不是可靠真源，不能直接覆盖正式分类。

```powershell
python scripts/import_screenshot_ocr.py --image path\to\screenshot.png --timestamp 20260625T210000+0800
```

如果已有 OCR 文本，可以作为 text override 输入：

```powershell
python scripts/import_screenshot_ocr.py --image path\to\source.png --text-override data\imports\ocr\ocr_text.txt --timestamp 20260625T210000+0800
```

如果 OCR 文本已经包含 `B.01-动作战斗` 这类集合标题，可以直接生成分类 preview：

```powershell
python scripts/import_classification_text.py --input data\imports\ocr\ocr_text.txt --timestamp 20260625T220000+0800
```

中文 OCR 名称需要明确绑定 appid 时，使用本地 alias map：

```powershell
python scripts/import_classification_text.py --input data\imports\ocr\ocr_text.txt --alias-map data\imports\ocr\alias_map.json --timestamp 20260625T220000+0800
```

这个脚本只把安全匹配写入分类 preview；未解析、冲突或重复项会进入 review preview。

如果来源是 JSON，且已经包含分类 slug 和明确的 `steam:<appid>` 映射，使用 JSON 导入器。它支持多个顶层 JSON 对象连续拼接的文件，并以 appid 作为身份真值：

```powershell
python scripts/import_classification_json.py --input data\imports\ocr\classification_raw.json --timestamp 20260626T010000+0800 --fetch-appdetails
python scripts/import_classification_json.py --timestamp 20260626T010000+0800 --apply-preview
```

正式写入 `data/manual/classifications.jsonl` 前必须先生成 preview。确认没有误匹配和冲突后再 apply。

## 人工确认分类

正式分类保存在 `data/manual/classifications.jsonl`。写入前先生成 preview：

```powershell
python scripts/apply_confirmed_classifications.py --input confirmed_classifications.jsonl --timestamp 20260625T213000+0800
```

检查 preview 后应用：

```powershell
python scripts/apply_confirmed_classifications.py --apply-preview data\manual\classifications.preview.20260625T213000+0800.jsonl
```

日常纠错或单独改分类，使用交互式编辑器：

```powershell
python scripts/edit_classification.py
```

它可以按 appid、`steam:<appid>`、名称定位游戏，也可以先按当前类别或状态列出游戏，再选择行修改。新类别可以输入 `1`-`17`、`B.04`，或 `narrative_adventure` 这样的 slug；输入 `clear` 会清空 B 类并把记录标为 pending。

例子：

```powershell
python scripts/edit_classification.py --appid 1272840 --new-category B.04
python scripts/edit_classification.py --from-category action_combat
python scripts/edit_classification.py --from-category pending --list-only
```

## 校验

```powershell
python scripts/validate_data.py
```

校验内容包括 JSONL 语法、`game_id` 格式、taxonomy 引用、重复记录、收藏状态规则，以及未分类 warning。

## 生成 CSV 视图

```powershell
python scripts/build_views.py
```

生成文件：

- `data/derived/games_flat.csv`
- `data/derived/category_stats.csv`
- `data/derived/backlog_stats.csv`

不要手工编辑 `data/derived/` 里的文件；重新运行 `build_views.py` 生成。

## 统计报告

打印完整 Markdown 统计报告：

```powershell
python scripts/report_stats.py
```

常用变体：

```powershell
python scripts/report_stats.py --limit 50
python scripts/report_stats.py --format json
python scripts/report_stats.py --write-derived
```

报告包括库存总量、分类数量、各分类游戏数、总/平均/中位游玩时间、分类覆盖率、个人状态、收藏、最近新增、最近游玩、最长游玩、pending/未分类，以及最新两次 Steam 快照差异。加上 `--write-derived` 后，会把 JSON 和 CSV 报告写到 `data/derived/reports/`。

## 隐私和安全

- 不要提交 Steam API key。使用 `.env`、环境变量或 `scripts/steam_api.local.json`。
- `.env`、原始导入、截图/OCR 材料、个人评价和购买记录默认被 git 忽略。
- 扫描 Steam 本地安装状态时只能只读，不修改 Steam 客户端配置。
- 不删除原始导入文件。
- 自动分类建议只进入 `data/suggestions/` 或 preview，正式分类必须人工确认。
