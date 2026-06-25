# 分类确认与导入流程

本项目的正式分类只写入 `data/manual/classifications.jsonl`。OCR、截图、文本、Steam 标签或自动建议都不能直接覆盖正式分类，必须先生成 preview，再检查并应用。

## 当前需要你确认的项目

这轮 B.01/B.02 文本导入已经安全写入 125 条正式分类：

- `action_combat` / `B.01`：77 条
- `shooter_competitive` / `B.02`：48 条

还需要你确认的是 `data/suggestions/unresolved_names.jsonl` 和 `data/suggestions/pending_review.jsonl`：

- `unresolved_names.jsonl`：35 条，脚本无法可靠匹配到当前 Steam 库中的 `game_id`。
- `pending_review.jsonl`：1 条，`Counter-Strike: Global Offensive` 与 `Counter-Strike 2` 都指向 Steam appid `730`，正式分类已保留一次，重复项留作人工记录。

### 未解析清单

这些条目需要你确认它们是否在你的 Steam 库里、对应的 appid 是什么，或者是否应暂时忽略：

```text
二战前线合集
红色沙漠
剑星
满头大汗了吧，击球手！
明末：渊虚之羽
女巫悲歌
蛇行武装
死亡教堂
幽灵行者 2 (Ghostrunner 2)
造梦西游：无双
Hitman: Absolution
HoloCure - Save the Fans!
ICEY
Iron Snout
Monster Hunter Wilds
Monster Hunter: World
Ninja Gaiden 4
Noita
Remember Me
Salt and Sanctuary
The Binding of Isaac: Rebirth
アリスのハニーハニークラッシュ！
彩虹六号：围攻
漫威争锋
命运2
枪火重生
枪途末路
上行战场 / The Ascent
战地风云™ 6
Evolve Stage 2
Left 4 Dead
Sniper Elite 4
Sven Co-op
The Expendabros
Thunder Tier One
```

其中 `Left 4 Dead` 和 `Sniper Elite 4` 只有相似候选，脚本没有自动确认，避免误写成 `Left 4 Dead 2` 或 `Sniper Elite 5`。

## 你确认时给什么格式

最稳妥的格式是 JSON 对象，用 OCR 名称映射到 `game_id` 或 Steam appid。可以直接发给我，也可以保存成 `data/imports/ocr/<timestamp>_alias_map.json`：

```json
{
  "彩虹六号：围攻": "steam:359550",
  "命运2": "steam:1085660",
  "Left 4 Dead": "steam:500"
}
```

如果你不确定 appid，也可以给英文 Steam 名称，但我会再次生成 preview，不会直接写正式分类：

```json
{
  "上行战场 / The Ascent": "The Ascent",
  "枪火重生": "Gunfire Reborn"
}
```

明确不在库里或不想处理的条目可以这样标注：

```json
{
  "红色沙漠": null,
  "剑星": null
}
```

人工确认后会写入两个正式文件：

- `data/manual/aliases.jsonl`：把 OCR 名称、中文名、英文名等作为 `game_id` 的别名。
- `data/manual/name_resolution.jsonl`：记录 `raw_name -> game_id` 的人工确认解析结果，保留来源、置信度和解析时间，便于审计。

## 后续类别文本导入格式

后续你给 B.03、B.04、A.07 等 OCR 结果时，推荐用纯文本，一个来源一轮，只放一种来源，不要把 Steam API、截图 OCR、人工修正混在同一轮。

格式：

```text
B.03-🧙角色扮演

The Witcher 3: Wild Hunt
赛博朋克 2077
Persona 5 Royal

B.04-♟️策略战术

Sid Meier's Civilization VI
Into the Breach
```

导入命令：

```powershell
python scripts/import_classification_text.py --input data\imports\ocr\b03_b04_text.txt --timestamp 20260625T230000+0800
```

如果有中文名需要人工绑定 appid，加 alias map：

```powershell
python scripts/import_classification_text.py --input data\imports\ocr\b03_b04_text.txt --alias-map data\imports\ocr\b03_b04_alias_map.json --timestamp 20260625T230000+0800
```

检查 preview 后再应用：

```powershell
python scripts/apply_confirmed_classifications.py --apply-preview data\manual\classifications.preview.20260625T230000+0800.jsonl
python scripts/build_views.py
python scripts/validate_data.py
```

## JSON 分类源导入格式

如果来源已经是 `分类 slug -> 游戏名 -> steam:<appid>`，推荐用 JSON。appid 是真值，游戏名只用于别名和审计；脚本会结合现有 `games.jsonl`、Steam owned CSV、Steam appdetails 来纠正标题。

格式可以是一个 JSON 对象：

```json
{
  "role_playing": {
    "ocr_collection": "B.03-🧙角色扮演",
    "items": {
      "艾尔登法环": "steam:1245620",
      "博德之门3": "steam:1086940"
    }
  }
}
```

也可以是多个顶层 JSON 对象连续拼接在同一个文件里。导入命令：

```powershell
python scripts/import_classification_json.py --input data\imports\ocr\classification_raw.json --timestamp 20260626T010000+0800 --fetch-appdetails
```

检查 preview 后应用：

```powershell
python scripts/import_classification_json.py --timestamp 20260626T010000+0800 --apply-preview
python scripts/build_views.py
python scripts/validate_data.py
```

脚本会生成这些 preview：

- `data/manual/games.preview.<timestamp>.jsonl`
- `data/manual/aliases.preview.<timestamp>.jsonl`
- `data/manual/name_resolution.preview.<timestamp>.jsonl`
- `data/manual/classifications.preview.<timestamp>.jsonl`
- `data/suggestions/pending_review.preview.<timestamp>.jsonl`

如果 appid 已有正式 B 类且与 JSON 分类冲突，脚本只写入 pending review，不覆盖正式分类。

## A 类状态怎么导入

当前 `scripts/import_classification_text.py` 主要用于 B 类主玩法分类。A 类状态和收藏建议先不要和 B 类混在一轮里。

A 类建议格式可以先这样发给我，后续我会按同样 preview 机制写入：

```text
A.07-🆕未启清单

游戏名 1
游戏名 2

A.03-💖心选佳作

游戏名 3
```

规则：

- B 类：每个游戏最多一个 `primary_category`。
- A 类 `curation_state`：每个游戏最多一个。
- `favorite=true` 时，`curation_state` 必须是 `handpicked`。
- A 类、B 类分开导入，便于回滚和审查。
