# AGENTS.md

Repo: `052-DocIndex` — GitHub Issues 为本项目的任务跟踪系统。

## Agent skills

### Issue tracker

Issues 与 specs 存放在本仓库的 GitHub Issues（`angelababyhuang/052-DocIndex`），所有操作走 `gh` CLI。见 `docs/agents/issue-tracker.md`。

### Triage labels

使用默认的五个 triage 角色标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。见 `docs/agents/triage-labels.md`。

### Domain docs

single-context 布局：`GLOSSARY.md` 与 `docs/adr/` 放在仓库根。见 `docs/agents/domain.md`。

## 开发

- 本机 Python 用 anaconda（`/Applications/anaconda3/bin/python3`，3.10）；CI 用系统 Python 3.12。
- 跑检查：`./check.sh`（ruff + pytest + compileall，CI 也走它）。CLI：`python -m docarchive <命令>`（命令见 `--help`）。
- WeKnora 接口契约与实测坑：`docs/weknora-api.md`（改 `weknora.py` / `index.py` 前先读它，别重新去翻网页文档）。
- 运行时依赖见 `requirements.txt`，测试依赖见 `requirements-dev.txt`。
