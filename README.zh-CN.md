# MyGameStory

English documentation: [README.md](README.md)

`MyGameStory` 是一个本地 Steam 游戏库数据项目。它用简单、可审计、可 git diff 的文件保存源数据，并自动生成适合 Excel 浏览和后续分析的 CSV 视图。不使用复杂 SQL 数据库。

分类确认和后续导入格式见：[docs/classification_workflow.zh-CN.md](docs/classification_workflow.zh-CN.md)

## 数据模型

- `data/manual/*.jsonl`：人工维护的源数据，包括游戏身份、分类、别名、个人评价和购买记录。
- `data/imports/`：Steam API、截图、OCR、本地集合导出的原始导入文件。原始导入只追加保存，不覆盖。
- `data/snapshots/*.jsonl`：从原始导入解析出的历史快照，例如库存、游玩时长、成就和安装状态。
- `data/suggestions/*.jsonl`：机器、OCR、导入流程产生的待审核建议。
- `data/derived/*.csv`：自动生成的宽表和统计表。不要手工编辑。

JSONL 文件里不写注释。字段说明、流程和注意事项放在 README 和脚本里。

## 主键设计

Steam 游戏使用 `game_id = "steam:<appid>"`，例如 `steam:292030`。中文名、英文名、Steam 商店名、OCR 文本都只是标题或别名，不能作为主键。

分类主键使用稳定英文 slug，例如 `role_playing`。`B.03` 这类 legacy code 和中文显示名只是展示层信息。

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

## 隐私和安全

- 不要提交 Steam API key。使用 `.env`、环境变量或 `scripts/steam_api.local.json`。
- `.env`、原始导入、截图/OCR 材料、个人评价和购买记录默认被 git 忽略。
- 扫描 Steam 本地安装状态时只能只读，不修改 Steam 客户端配置。
- 不删除原始导入文件。
- 自动分类建议只进入 `data/suggestions/` 或 preview，正式分类必须人工确认。
