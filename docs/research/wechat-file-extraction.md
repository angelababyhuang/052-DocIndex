# Extracting Files from WeChat on macOS (微信 / WeChat 4.x)

Research report for the 052-DocIndex document-archiving system.
Target machine: macOS 27.0.1 (build 26A434), WeChat **4.2.0** (App Store build), container `com.tencent.xinWeChat`.

**Method note.** Findings marked **[verified locally]** were confirmed on the user's own machine by inspecting the
installed WeChat binary (`/Applications/WeChat.app/Contents/Resources/wechat.dylib` via `strings`), reading code
signing entitlements, and probing filesystem access. Everything else is sourced from primary documentation and
source repositories, cited inline. Anything I could not confirm on this machine is marked **UNVERIFIED** rather than
guessed.

**Priority order of this report** (per request): §1 file layout → §2 sandbox/ingestion strategies → §3 the
folder-based pattern with dedup signals → §4 WeCom alternatives → §5 tools → §6 DB internals (reference).

---

## 1. Where WeChat stores received files on macOS (WeChat 4.x)

### 1.1 The layout

Root of the 4.x data tree:

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
```

Below that, one directory per logged-in account; the folder name carries a trailing hash, e.g.
`wxid_xxxxxxxxxxxxxxxx_<hash>`. Per-account structure:

| Path | Contents |
|---|---|
| `<account>/msg/file/` | **Received documents/files — this is what a document archiver wants** |
| `<account>/msg/attach/` | Images; bucketed by `md5(peer_username)`, then by month |
| `<account>/msg/video/` | Video messages |
| `<account>/db_storage/` | Encrypted SQLite (messages, contacts, sessions, favorites) |
| `<account>/hardlink/` | Hardlink index tables: md5 → stored file path |
| `<account>/cache/` | Service caches (e.g. Moments) |
| `<account>/temp/head_image/` | Avatars |
| `xwechat_files/all_users/` | Cross-account data (`login/<wxid>/` lives here) |

**This exact set of directory names was confirmed directly in the installed WeChat 4.2.0 binary [verified locally].**
`strings` over `wechat.dylib` yields the standalone path tokens `all_users`, `db_storage`, `message`, `session`,
`contact`, `favorite`, `login`, `attach`, `file`, `video`, `hardlink`, `cache`, `temp`.

Three independent secondary sources agree:

- Mole's WeChat 4 rules table: `msg/file` → "收到的文件" (received files); `msg/attach` + `msg/video` →
  "按月份存放的聊天图片和视频" (monthly-partitioned chat images and videos); `db_storage` → "聊天数据库"
  ([mole.fit/zh/tested-apps/wechat](https://mole.fit/zh/tested-apps/wechat)).
- The `wechat-attachment-organizer` skill lists macOS 4.x as
  `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<账号>/msg/` with sources
  `msg/file`, `msg/attach`, and `.dat` images
  ([clawhub.ai/samjoeyang/skills/wechat-attachment-organizer](https://clawhub.ai/samjoeyang/skills/wechat-attachment-organizer)).
- [CSDN — MAC版微信文件存储路径](https://blog.csdn.net/m0_63814634/article/details/148789158).

### 1.2 The three sub-answers

**(a) Files received in chats** → `<account>/msg/file/`. The on-disk name is the original filename; the
per-conversation partition comes from the `hardlink` tables (§6.3), which store `file_name` plus directory ids.

**(b) "Chat Files" / 聊天文件** → **a virtual UI view over the same `msg/` tree, not a separate store.** The
binary contains the UI string `fatal pachat_files_page_{"chat_files_tabchat_files_page`, the localisation
"Manage all chat files in \"Chat Files\"", "Open chat files in read-only mode", and "Enable to protect chat files
from being modified" [verified locally]. So 聊天文件 filters the existing per-account `msg/file` + `msg/attach` +
`msg/video` content.

**(c) Images/videos cached** → `msg/attach/<md5(peer_username)>/<YYYY-MM>/…` and `msg/video/…`.
`media_helpers.py` in
[`LifeArchiveProject/WeChatDataAnalysis`](https://github.com/LifeArchiveProject/WeChatDataAnalysis/blob/main/src/wechat_decrypt_tool/media_helpers.py)
confirms the bucketing: `root / "msg" / "attach" / hashlib.md5(username.encode("utf-8")).hexdigest()`.
Image filenames are **not** originals — they are `<file_name>.dat`, `<file_name>_t.dat` (thumb), `<file_name>_h.dat`,
`<file_name>_W.dat`, falling back to `<local_id>_<create_time>.dat`
([`tqjason/WeChatMsg` `wxManager/db_v4/hardlink.py`](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py)).
Note `.dat` files are XOR-obfuscated and need decoding to restore real format + extension.

**(d) The message database** → `<account>/db_storage/`, encrypted SQLite — see §6.

### 1.3 A naming wrinkle that breaks naive scripts [verified locally]

The installed 4.2.0 binary contains **both** `xweChat_files` and `WeChat Files`, plus this migration prompt:

> `Chat history from previous versions detected in Weixin data directory. Rename "xweChat_files" to "WeChat Files" to continue.`

So 4.x has used more than one folder name, and the 3.x-style `WeChat Files` is being reused as the 4.x name in some
builds. **Do not hard-code the folder name.** Discover it instead:

```python
# https://github.com/rmqg/wechat-mac-export/blob/main/wxexport/detect.py
glob.glob(os.path.join(data_dir, "*", "db_storage"))
```

or generically:

```bash
find ~/Library/Containers -maxdepth 6 -type d -name db_storage 2>/dev/null
```

(per [`robbin/wechat-exporter` `agent-decrypt-guide.md` §3.1](https://github.com/robbin/wechat-exporter/blob/main/references/agent-decrypt-guide.md)).

### 1.4 Historical WeChat 3.x data

WeChat 3.x stored data in a different tree:
`~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/<version>/<account>/`
with media in `Message/MessageTemp` ([mole.fit](https://mole.fit/zh/tested-apps/wechat)). If the archive must also
cover pre-4.x history, that tree needs its own migration step. (The 4.x binary still references
`Message/MessageTemp`-style legacy handling.)

---

## 2. Can a third-party script read the container? And what are the realistic ingestion strategies?

### 2.1 Direct answer on the sandbox

**No — not without Full Disk Access.** The parent agent independently confirmed "Operation not permitted" on the
container. My own probe adds the *mechanism*, which determines the workaround:

```
os.stat('.../Data')              → SUCCEEDS   (metadata readable)
os.stat('.../Data/Documents')    → SUCCEEDS   (metadata readable)
os.listdir('.../Data/Documents') → FAILS, OSError errno=1 (EPERM)
open('.../Data/Documents/xwechat_files') → FAILS, EPERM
```

**This is macOS TCC, not POSIX permissions.** The directories are `drwx------` owned by uid 501 (the user), so a
plain POSIX permission check would have *allowed* access. `EPERM` on `open`/`listdir` while `stat` succeeds is TCC
refusing reads of app-container contents. macOS 27 applies this to app containers generally, not only to the owning app.

**WeChat is App Sandboxed** (`codesign -d --entitlements - /Applications/WeChat.app`) [verified locally]:

```
com.apple.security.app-sandbox                → true
com.apple.security.application-groups         → [5A4RE8SF68.com.tencent.xinWeChat]
com.apple.security.files.user-selected.read-write → true
com.apple.security.files.downloads.read-write     → true
com.apple.security.network.client/server     → true
```

**Practical consequence:** a third-party script cannot read
`~/Library/Containers/com.tencent.xinWeChat/Data/` unless the executing binary (Terminal/iTerm/Python/the agent's
host app) is granted **Full Disk Access**, or the code runs under WeChat's own sandbox profile.

**The useful asymmetry:** WeChat holds `files.user-selected.read-write` and `files.downloads.read-write`. So
**WeChat itself can write files out of its container** into `~/Downloads` or a user-picked folder — and those
locations are readable by ordinary processes with **no FDA at all**. This is the cleanest way around the whole
problem, and it is the basis of the recommended strategy in §3.

### 2.2 There is no supported CLI

**WeChat for Mac exposes no documented or supported command-line interface** for exporting files or messages. There
is no automation surface. Everything is either (a) reading the on-disk container, or (b) driving the GUI.

### 2.3 Realistic ingestion strategies

| # | Strategy | macOS 27 viable | Needs FDA | Legal risk | Fidelity | Burden |
|---|---|---|---|---|---|---|
| **A** | **User saves into a watched folder → Folder Action/Shortcut ingests** | ✅ Best | ❌ No | ✅ None | Files only | Low (one human habit) |
| **B** | GUI-automation of Save-to-Computer into a watched folder | ✅ Good | ❌ No | ✅ None | Files only | Medium (AppleScript/UI scripting) |
| **C** | Direct DB parsing (decrypt `db_storage`) | ⚠️ Fragile | ✅ Yes + re-sign + SIP | ⚠️ Real | Full metadata | High; breaks on updates |
| **D** | 企业微信 会话内容存档 (official archive API) | ✅ Best if WeCom in scope | ❌ No | ✅ None (official) | Full, official | Low–medium; needs non-macOS host |
| **E** | 腾讯文档 Open API | ✅ Best | ❌ No | ✅ None (official) | Full, official | Low |
| **F** | 收藏 / `favorite.db` | ⚠️ Fragile | ✅ Yes | ⚠️ Moderate | Favorites only | High |

**Recommendation: A first, then E, then D if WeCom traffic is in scope. Keep C for one-off historical backfill only.**

Strategy C's hard requirements (each a real cost):
- **Full Disk Access** for the decrypting process, or a copy made by a process that already has it.
- **Re-signed WeChat** to strip Hardened Runtime so `task_for_pid` memory reads succeed:
  `sudo codesign --force --deep --sign - /Applications/WeChat.app` — or SIP disabled
  (`csrutil disable`, larger blast radius). Re-signing changes the app's signature identity, so Keychain items,
  granted system permissions (mic/camera/screen recording) and auto-update may all need re-authorisation, and it
  must be redone after **every** WeChat update.
- Correct WAL handling and correct key-shape branch determination (§6).
- WeChat 4.x updates change the DB format and key-extraction technique; `rmqg/wechat-mac-export` explicitly notes
  memory scanning stopped working on 4.1.11, which they worked around with an lldb breakpoint on CommonCrypto.

**Also relevant: the legal climate has turned hostile.** `sjzar/chatlog` (9.1k★, Go) was deleted 2025-10-20 after
receiving a legal letter from 微信官方; its repo now contains only a removal notice. The same fate is reported for
MemoTrace/留痕, WeChatBackup and PyWxDump ([V2EX 1167055](https://global.v2ex.co/t/1167055)). Strategy C therefore
carries genuine legal exposure for a shipped system.

---

## 3. The folder-based pattern — concrete working approach

### 3.1 Proposed layout

```
~/Documents/WeChatInbox/            ← user drops saved files here (OUTSIDE the container)
    2026-11-05/
        合同_v2.pdf
        预算表.xlsx

~/Documents/WeChatInbox/_processed/  ← Folder Action moves+renames here after hashing
```

Recommended full pipeline:

```
WeChat "保存到电脑" / Save to computer
        ↓  (user action, or AppleScript over Chat Files window)
~/Documents/WeChatInbox/
        ↓  Folder Action (macOS, no FDA needed)
        ↓  1. detect new files (mtime + size heuristic, or a state file)
        ↓  2. compute SHA-256
        ↓  3. look up SHA-256 in archive manifest → skip if already ingested
        ↓  4. copy (never move) into the archive store, content-addressed
        ↓  5. write manifest row: sha256, size, md5, orig_name, first_seen, source_path
        ↓  6. move the inbox file to _processed/ (or leave it; the manifest makes re-runs idempotent)
archive/<sha256[0:2]>/<sha256>.<ext>   ← content-addressed store
archive/manifest.jsonl                 ← one JSON object per archived file
```

### 3.2 macOS automation mechanisms for step 2

- **Folder Actions** — Finder → Services → Folder Actions → attach a script to the inbox folder.
  Apple reference: [Folder Actions AppleScript reference](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_folder_actions.html),
  [Mac Automation Scripting Guide: Watching Folders](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/WatchFolders.html).
- **Shortcuts** — a Shortcut triggered by a file-add event that runs a shell script. Good for normalising names and
  calling a Python ingester.
- **Plain daemon** — a `launchd` agent polling the inbox; simplest and most controllable. (Per this machine's
  conventions: single instance, single worker.)

All three watch a **non-container** folder, so none of them inherit the TCC problem.

**Note on the WeChat side [verified locally]:** the binary contains the literal UI string **`Save to computer`**
(and "Save as"), confirming the manual-save action exists in this build. There is **no** visible configurable
"auto-save received files to folder" preference key in the binary (searched for `download_path`, `FileSavePath`,
`ChatFilePath`, `save_path`, `DefaultDownloadDir` — none found; only a generic `FilePath`/`file_path`). So the
auto-save pattern should be treated as **UNVERIFIED for WeChat 4.2.0 macOS** and validated in the app's
Settings → 通用设置 / 存储空间 UI before relying on it. The manual Save-to-Computer habit (or UI automation of it)
is the reliable path.

### 3.3 Do received-file names carry dedup signals? — Yes, and the `(1)` suffix means the **opposite** of what it looks like

Per a Tencent WXG (微信事业群) engineer, 客村小蒋, about the desktop and mobile clients:

> 电脑端和手机端收到同一份文件多次转发，**都只会保存一次**，文件后缀加（1）、（2）只是为了避免冲突，
> **并不是真实存储了多次**。
> 检测是否是同一个文件的方法，**是校验文件大小和 md5 值**。

([IT之家, 2024-11-26](https://m.ithome.com/html/813218.htm); also
[Pcdaily](https://www.pcd.com.cn/redian/202411/21510.html))

So:
- The `(1)` / `(2)` suffix is a **display-level collision-avoidance marker, not a second stored copy.**
- The file is stored **once**.
- **WeChat's own dedup key is (file size, MD5).**
- A *format* change (e.g. a video gaining subtitles) makes WeChat treat it as a **different** file and store it again.

**Corroborated in the installed binary [verified locally]:** `wechat.dylib` contains the literals `filemd5`,
`filedatamd5`, `extern_md5`, `blockmd5`, `FileName`, `fileNamemimeTypedecryptKfileMd5`, plus the error strings
`FileMd5NotEqual` and `FileDataMd5NotMatch` — the client maintains MD5-based file identity and compares it.

Original filenames **are** preserved on disk: `file_hardlink_info_v3.file_name` → `msg/file/<dir1>/<file_name>`
([`tqjason/WeChatMsg` `hard_link.py`](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py)).

### 3.4 Stable hashes / msg ids available for dedup

| Signal | Where | Stability | Use |
|---|---|---|---|
| **File MD5** | `file_hardlink_info_v3.md5`, `image_hardlink_info_v3.md5`, `video_hardlink_info_v3.md5`; also message XML `<img md5=…>` / `<videomsg md5=…>` | Stable per content across chats/devices; changes on re-encode | **Primary WeChat-native dedup key** (with size) |
| **File size** | `file_hardlink_info_v3.file_size` | Stable | Pairs with MD5 |
| **`msg_id`** | message table | Unique per message instance | Message-level idempotency |
| **`create_time`** | message table (unix seconds) | Per message | Ordering, incremental watermark |
| **`_rowid`**, **`modify_time`** | hardlink tables | Per row | Change detection |
| **Conversation id** | `md5(username)` → `Msg_<md5>` table | Stable | Partitioning |
| **SHA-256 of bytes** | computed by you post-extraction | Stable | **Recommended** archive-layer dedup key |

### 3.5 Recommended dedup strategy — two layers

1. **WeChat-native layer:** `(file_size, md5)` — reproduces WeChat's own semantics exactly, and naturally collapses
   the same file received in N chats to one copy.
2. **Archive layer:** **SHA-256 over the extracted bytes** — survives re-encoding, catches corruption, enables a
   content-addressed store.

**Never dedup on filename** — `(1)` suffixes make it unreliable, and different files can legitimately share a name.
The `wechat-attachment-organizer` skill takes exactly this approach: "**仅当两个文件哈希值相同才视为重复，绝不比较文件名**"
([ClawHub](https://clawhub.ai/samjoeyang/skills/wechat-attachment-organizer)).

Keep a manifest keyed by `(md5, size)` → `(sha256, canonical path, first_seen_msg_id)` so re-running ingest is
idempotent and every archived file traces back to the message that introduced it.

### 3.6 Practical note on the manual-save pattern

The user-side habit is the friction point. Two mitigations:
- WeChat's Chat Files view shows everything received; the user can bulk-open/save from there rather than
  per-message.
- UI automation (AppleScript) of the Chat Files window can save without per-file human action — but note WeChat's own
  "Open chat files in read-only mode" / "Enable to protect chat files from being modified" toggles
  [verified locally] suggest some UI paths are deliberately locked down; the exact behaviour of the Save action under
  automation is **UNVERIFIED** and should be tested before depending on it.

---

## 4. 企业微信 / WeCom — the officially sanctioned alternative

### 4.1 会话内容存档 (Conversation Content Archive) — first-party, official

This is **not a workaround — it is the vendor's intended mechanism**, and it avoids all container/TCC decryption.

- Official docs: [获取会话内容](https://developer.work.weixin.qq.com/document/path/91774) ·
  [使用前帮助](https://developer.work.weixin.qq.com/document/path/91361)
- Requires an enterprise admin to enable the feature, plus: a **Corpid**, an archive **Secret**, the enterprise
  **RSA private key** (for message decryption), and the calling egress IP registered as a trusted IP.
- Flow: `get_chat_data(seq, limit)` → encrypted records → decrypt `encrypt_random_key` with the RSA private key →
  `decrypt_data(encrypt_key, encrypt_chat_msg)` → typed message models → **download media by `sdkfileid`** via
  chunked `iter_media_data(sdkfileid)` (each chunk returns `data`, `indexbuf`, `is_finish`).
- Batch limit is 1000 records per call; persist the last processed `seq` to resume.

**Blocker for this machine:** [`pywecomarchive`](https://pypi.org/project/pywecomarchive/) (MIT, released 2026-10-28),
a Python wrapper over the official native SDK, **explicitly does not support macOS**:

> 暂不支持 macOS、Windows ARM64、32 位系统和 Alpine Linux / musl。

Supported: Windows x64, Linux glibc x86_64, Linux ARM64. So on macOS you would need a Windows/Linux collector host,
or to drive the official C SDK directly if Tencent ships a macOS build (**UNVERIFIED**). Also available:
[`wecomarchive`](https://pypi.org/project/wecomarchive/0.2.4/) and
[`pywecomarchive` on GitHub](https://github.com/911061873/pyWecomArchive).

**WeCom 微盘 (Drive) API** also exists — [获取文件列表](https://developer.work.weixin.qq.com/document/path/93657) —
for files in the enterprise drive: a cleaner ingest path than any local file scraping.

**Status on this machine: 企业微信 is NOT installed** [verified locally] — no `/Applications/企业微信.app` and no
`~/Library/Containers/com.tencent.WeWorkMac`. So this is a recommendation for the *architecture*, contingent on
where the documents actually arrive from. WeCom's local macOS storage path is **UNVERIFIED** (app absent).

### 4.2 腾讯文档 (Tencent Docs) — official export API

[接口索引](https://docs.qq.com/open/document/saas/openapi/file/) exposes a full Open API for the SaaS product:

- File APIs: `files:list`, `files:search`, `files:create`, `files/{fileId}` get/update/delete,
  `files/{fileId}:copy`, `files/{fileId}:move`, `files/{fileId}:recover`
- Async task APIs: **`files/{fileId}:export`** (导出文件), `files:upload`, `files:import`, `tasks/{taskId}` poll,
  `tasks/{taskId}:cancel`, **`tasks/{taskId}:convertAndExportPDF`** (DOC 转 PDF)

Also [MCP interface](https://docs.qq.com/open/document/saas/mcp.html) and a
[Skill](https://docs.qq.com/open/document/saas/skill.html) for 腾讯文档 SaaS.

So a document living in 腾讯文档 can be exported programmatically with the vendor's blessing — far better than
scraping the container for a link to it.

### 4.3 收藏 (Favorites)

Favorites live in a local **SQLCipher-4 database `favorite.db`** under `db_storage` — the binary contains the literal
`favorite` directory token [verified locally]; `wechat-favorites-rescue` confirms `favorite.db` is SQLCipher-4
encrypted.

- **No official export API.** That tool states it plainly: "官方没有导出按钮" (no official export button).
- Favorite `type` codes: `1`=chat plain text, `2`=file, `3`=voice, `5`=公众号文章, `8`=mini-program,
  `14`=link/chat record, `18`=视频号.
- Requires the same key-extraction machinery as §6 — same TCC + re-signing constraints.
- The tool's platform note: "**Windows**：完整支持（主力环境）; **macOS**：脚本已跨平台化（自动探测容器路径），
  但需 Mac 微信登录产生数据". It also depends on external `wechat-decrypt` and `wechat-mcp-server` components that
  are separately unavailable.

---

## 5. Open-source extraction tools

### 5.1 ⚠️ The landscape changed — the popular tools were removed under legal pressure

**`chatlog` (`sjzar/chatlog`) was deleted in October 2025 after receiving a legal letter from WeChat (微信官方).**
The repo now contains only a removal notice ([github.com/sjzar/chatlog](https://github.com/sjzar/chatlog)):

> 我收到了来自微信官方的函件，明确项目的核心功能存在合规风险。为了彻底规避法律问题，并本着对开源社区负责任的态度，
> 我决定 **移除本项目的全部代码与提交历史记录。**

It was previously 9.1k★, Go, MIT-licensed. The code survives in the GitHub *fork network* — the community documented
recovering it via `git fetch origin <sha> && git reset --hard <sha>` before the push-to-delete
([V2EX 1167055](https://global.v2ex.co/t/1167055)). Forks remain, e.g.
[`Symb0x76/chatlog`](https://github.com/Symb0x76/chatlog) (Apache-2.0, 70★, archived).
**The same V2EX thread reports identical fates for MemoTrace (留痕), WeChatBackup and PyWxDump.**

### 5.2 Tool table

| Tool | Lang | License | macOS? | Status | Notes |
|---|---|---|---|---|---|
| [`rmqg/wechat-mac-export`](https://github.com/rmqg/wechat-mac-export) | Python | GPL-3.0 / MIT | ✅ **macOS only**, WeChat 4.1.x | Active 2026-09 | **Most macOS-specific.** lldb breakpoint on CommonCrypto because memory scanning broke on 4.1.11. Needs SIP off + re-signed WeChat + sudo. Exports HTML/CSV/text. |
| [`ydotdog/wechat-export-macos`](https://github.com/ydotdog/wechat-export-macos) | Python + C | WTFPL | ✅ macOS (Apple Silicon & Intel), 4.x | Active 2026-04 | Mach VM memory scan for `x'<64hex><32hex>'`; AES-256-CBC per-page decrypt; exports TXT/CSV/JSON. Documents the re-signing rationale. |
| [`robbin/wechat-exporter`](https://github.com/robbin/wechat-exporter) | Spec (Markdown) | — | ✅ macOS 4.x | Active 2026-10 | **Not a program — a rigorous implementation spec.** `agent-decrypt-guide.md` is the best public documentation of the page format, HMAC, WAL handling, acceptance criteria. Its old upstream is gone. |
| [`lyrcsjy/wechat-decrypt`](https://github.com/lyrcsjy/wechat-decrypt) | Python | none stated | ✅ Win/macOS/Linux | Active 2026-07 | Toolbox: DB decrypt, image `.dat` decrypt (V1/V2/wxgf), Moments, SILK→WAV→ASR, Web UI, MCP server, live message monitor. Also 企业微信 5.x (wxSQLite3 AES-128). |
| [`jackwener/wx-cli-again`](https://github.com/jackwener/wx-cli-again) | Rust | ✅ **Apache-2.0** | ✅ macOS/Linux/Windows | Active 2026-09 | "fresh start from wx-cli" — post-takedown successor in spirit. Single Rust binary, daemon caching decrypted DBs, `attachments` command. ⚠️ README says source repo `botiverse/wx-cli` is now **private**. |
| [`tars1230/wechat-favorites-rescue`](https://github.com/tars1230/wechat-favorites-rescue) | Python | MIT | ⚠️ Win primary, macOS partial | Active 2026-08 | Decrypts `favorite.db` → exports 收藏 as a knowledge base. Depends on external now-unavailable components. |
| [`328336690/wechat-decrypt`](https://github.com/328336690/wechat-decrypt) | — | MIT | ❌ Windows 4.0 | 2026-06 | Memory-based key extraction, Windows-oriented. |
| [`tqjason/WeChatMsg`](https://github.com/tqjason/WeChatMsg) | Python | — | ❌ Win (code paths use `msg\\`) | Source available | **Valuable as a schema reference** — best documented source for the `hardlink_info_v3` tables and message XML structure. |
| [`Thearas/wechat-db-decrypt-macos`](https://github.com/Thearas/wechat-db-decrypt-macos) | C | none | macOS arm64, 4.1.2.241 | 2026-07 | **Author withdrew** — README is now just "本人遵纪守法，先溜了". |
| [`lc044/WeChatMsg`](https://github.com/LC044/WeChatMsg) (留痕) | Python | — | ❌ Win | **Dead** | Repo holds only `readme.md` + `doc/`; README: "此项目很久没有（也不会）更新了". 42k★ but no code. |
| [`MetaZhi/MemoTrace`](https://github.com/MetaZhi/MemoTrace) | — | MIT | — | 2025-04, 5★ | Mirror/ghost of MemoTrace. |
| [`xaoyaoo/PyWxDump`](https://github.com/xaoyaoo/PyWxDump) | Python | — | ❌ Win | Takedown/mirror | Reported as also deleted; forks preserve code. |
| [`Bryan-Cyf/WeChatDaily`](https://github.com/Bryan-Cyf/WeChatDaily) | Python | MIT | ❌ | 2026-07 | AI-agent skill + daily group report generator; bundles `tools/wechat-decrypt`. |

### 5.3 Recommendation

For a **document** archiver (files, not full chat history):

1. **`robbin/wechat-exporter`'s `agent-decrypt-guide.md`** — use as the authoritative spec to validate any
   implementation and to avoid its documented pitfalls (page-1 salt offset, WAL checkpoint return-value
   interpretation, salt-vs-path key mapping).
2. **`ydotdog/wechat-export-macos` (WTFPL)** for the mechanism if DB parsing is needed — permissive licence, macOS
   and Intel both supported. **GPL-3.0** on `rmqg/wechat-mac-export` is copyleft and matters if vendored into a
   distributed product; WTFPL is permissive but non-standard — verify terms.
3. **`jackwener/wx-cli-again` (Apache-2.0)** is the cleanest licence if a maintained CLI is wanted — but verify source
   availability, since the README says the upstream repo is private.

**Licence/compliance caveat:** treat this category as spare parts for private tooling, not a dependency to vendor into a
shipped product — the GPL/WTFPL mix plus the demonstrated legal pressure both point that way.

---

## 6. Message database internals (reference)

### 6.1 Location and format

Databases live in `<account>/db_storage/` as `<subdir>/<name>.db` with sibling `-wal` and `-shm` files. Subdirectories
include `message`, `session`, `contact`, `favorite`, `login` [verified locally].

**Encrypted: yes — WCDB (SQLCipher 4), AES-256-CBC, per-database key.**
[`ydotdog/wechat-export-macos`](https://github.com/ydotdog/wechat-export-macos):

> 微信使用 WCDB（基于 SQLCipher 4）加密本地数据库。每个 `.db` 文件有独立的 AES-256 密钥，缓存在进程内存中，
> 格式为 `x'<64hex_key><32hex_salt>'`。

Page format, from the most rigorous public spec
([`agent-decrypt-guide.md`](https://github.com/robbin/wechat-exporter/blob/main/references/agent-decrypt-guide.md)):

| Item | Value |
|---|---|
| Page size | 4096 bytes |
| Cipher | AES-256-CBC |
| Key length | 32 bytes (`enc_key`) |
| Per-page IV | 16 bytes |
| Per-page auth | HMAC-SHA512, 64 bytes |
| Per-page reserve | 80 bytes = IV(16) + HMAC(64) |
| Usable per page | 4016 bytes |

| Page | salt | ciphertext | IV | HMAC |
|---|---|---|---|---|
| Page 1 | `[0,16)` | `[16,4016)` | `[4016,4032)` | `[4032,4096)` |
| Page 2+ | — | `[0,4016)` | `[4016,4032)` | `[4032,4096)` |

```
mac_salt = salt each byte XOR 0x3a
mac_key  = PBKDF2-HMAC-SHA512(enc_key, mac_salt, iterations=2, dkLen=32)

start  = 16 if page_no == 1 else 0      # page 1's salt is NOT authenticated
input  = page[start:4032] || page_no (4-byte little-endian)
value  = HMAC-SHA512(mac_key, input)
```

Page 1 decrypted output is reconstructed as `b"SQLite format 3\x00" + plain + b"\x00" * 80`.

**Two key-shape branches — version-dependent, and must be determined empirically:**

| Branch | Reported versions | Key source |
|---|---|---|
| **A** | 4.0.x and some 4.1.x | Raw ASCII `x'<64 hex><32 hex>'` in process memory; first 64 hex = `enc_key`, last 32 = salt |
| **B** | reported from 4.1.10 onward | Only a **passphrase** cached; `enc_key = PBKDF2-HMAC-SHA512(passphrase, salt, 256000, 32)` |

The guide is explicit that branch detection is empirical: scan as A and validate candidates against the page-1 HMAC;
only if nothing matches, consider B. A zero-candidate scan must also be distinguished from insufficient permissions or
a target DB the client has not yet opened.

> **WeChat 4.2.0 on macOS 27: branch UNVERIFIED.** `rmqg/wechat-mac-export` is verified against **4.1.11** and
> states memory scanning "no longer works" there, using an lldb breakpoint instead. This must be determined
> experimentally on this machine before any DB-parsing work proceeds.

### 6.2 Message tables and columns

- **Sharding:** `db_storage/message/message_0.db`, `message_1.db`, … Each shard may hold **multiple conversation
  tables**. Scan *all* shards, including multi-digit numbers — a new conversation can land in a higher-numbered shard.
- **Conversation table:** `Msg_<md5(username)>` where `<md5>` = MD5 of the peer's `username` (group chats end in
  `@chatroom`).
- **Mapping table:** `Name2Id` with column `user_name` (plus `rowid`).

```python
import hashlib, sqlite3
conn = sqlite3.connect("file:message_0.db?mode=ro", uri=True)
for (username,) in conn.execute("SELECT user_name FROM Name2Id"):
    table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
    # check the table actually exists before querying
```

**Message columns** (all confirmed present as literals in the installed 4.2.0 binary [verified locally]):
`local_type`, `message_content`, `real_sender_id`, `create_time`, `msg_id`, `user_name`, `FileName`, `Reserved`.

| Column | Meaning |
|---|---|
| `local_type` | Type: 1=text, 3=image, 34=voice, 43/63=video, 47=sticker, 49=app/file/link, 10000=system |
| `message_content` | Payload: plain text for type 1; **XML** for media/app messages. Prefix form `sender_wxid:\n<payload>` |
| `real_sender_id` | FK into `Name2Id.rowid` — needed for group chats |
| `create_time` | Unix timestamp (seconds) |
| `msg_id` | Message id |

**Some BLOBs are zstd-compressed** — rows flagged `WCDB_CT_*` carry magic `28 b5 2f fd` and must be zstd-decompressed
before UTF-8 decoding (`rmqg/wechat-mac-export`'s `_bdecode()` is a good reference).

**Type 49 (app/file) XML** carries the attachment metadata. From
[`tqjason/WeChatMsg` `hard_link.py`](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py)):

```python
root.find(".//img").get("md5")        # image
root.find(".//videomsg").get("md5")   # video
```

### 6.3 The hardlink tables (message → file mapping)

This is the key to mapping a message to an actual file on disk:

```sql
select file_size, type, file_name, dir2id.username, dir2id2.username, _rowid_, modify_time, extra_buffer
from file_hardlink_info_v3
join dir2id on dir2id.rowid = dir1
LEFT JOIN dir2id AS dir2id2 ON dir2id2.rowid = dir2 AND dir2 != 0
where md5=?
```

Tables: **`image_hardlink_info_v3`**, **`video_hardlink_info_v3`**, **`file_hardlink_info_v3`**, each with an
indexed `md5` column. `dir2id` maps directory rowids → usernames. Path reconstruction:

```python
image_root_path = "msg/attach/"
video_root_path = "msg/video/"
file_root_path  = "msg/file/"

# files (type != 6):
filepath = os.path.join(file_root_path, dir1, file_name)     # dir1 = dir2id.username
# videos (type == 5):
filepath = os.path.join(video_root_path, dir1, dir2, 'Rec', dir3, 'V', file_name)  # dir3 from protobuf in extra_buffer
```

**Caveat — UNVERIFIED on macOS:** this schema is Windows-shaped (backslash roots, `dir1`/`dir2`/`dir3` columns).
Whether `file_hardlink_info_v3` exists and is populated in the macOS 4.2.0 `db_storage` is **unconfirmed**.
`rmqg/wechat-mac-export` (macOS-native) reads only `message/message_*.db` and does **not** use the hardlink tables.
Check against the actual decrypted DBs before relying on it.

### 6.4 WAL handling (must not be skipped)

Nearly every `.db` has a sibling `-wal`; ignoring it silently loses the newest transactions. The guide's default
route is to let SQLCipher do it on a disposable copy:

```sql
PRAGMA key = "x'[enc_key][salt]'";
PRAGMA cipher_compatibility = 4; PRAGMA cipher_page_size = 4096;
PRAGMA wal_checkpoint(PASSIVE);
```

Acceptance rule: success requires `busy == 0` **AND** `log == checkpointed`. `busy == 0` alone is insufficient — a
blocking reader yields `(0,5,3)`. Also note `TRUNCATE` reports `(0,0,0)` after truncating, so it cannot be used to
infer how many frames were pending.

---

## 7. Recommended plan for 052-DocIndex

1. **Ship strategy A + Folder Actions/Shortcuts first.** WeChat saves into `~/Documents/WeChatInbox/` (outside the
   container, no FDA). A Folder Action or Shortcut computes SHA-256, looks up the manifest, copies into a
   content-addressed store, and updates `manifest.jsonl`. Unimpeded by TCC, immune to WeChat updates, zero legal
   exposure. Keep the manifest so re-runs are idempotent.
2. **Add the 腾讯文档 Open API** for anything that is a 腾讯文档 link — official, clean, and it yields metadata the
   container cannot.
3. **Evaluate 企业微信 会话内容存档** if WeCom traffic is in scope — the only route that is both complete and
   sanctioned. Budget for a Windows/Linux collector host, because the Python SDK ships no macOS wheel.
4. **Keep DB parsing (C) as an offline historical backfill only.** Use `agent-decrypt-guide.md` as the spec;
   `ydotdog/wechat-export-macos` or `rmqg/wechat-mac-export` as the implementation. Respect the WAL rules and the
   branch A/B determination. Note the GPL-3.0 on `rmqg` and the vendor's demonstrated legal pressure on this category.
5. **Dedup at two layers:** WeChat-native `(size, md5)` from the hardlink tables, plus SHA-256 over extracted bytes.
   **Never dedup on filename.**
6. **Discover the container path dynamically** (`glob` on `*/db_storage`) — the folder name differs between 4.x
   builds (`xwechat_files` vs `WeChat Files`).

---

## Sources

**Primary / local [verified locally on this machine]**
- WeChat 4.2.0 binary `/Applications/WeChat.app/Contents/Resources/wechat.dylib` — `strings` extraction of directory
  tokens, DB column names, and UI strings (`Save to computer`, `chat_files_tab`, `xweChat_files` / `WeChat Files`)
- `codesign -d --entitlements - /Applications/WeChat.app` — App Sandbox entitlements
- Filesystem probe of `~/Library/Containers/com.tencent.xinWeChat/Data/` — `stat` succeeds, `listdir`/`open` → EPERM
- macOS 27.0.1 (26A434); WeChat 4.2.0; 企业微信 not installed

**Repository / specification**
- [rmqg/wechat-mac-export](https://github.com/rmqg/wechat-mac-export) — macOS WeChat 4.x decrypt/export
- [robbin/wechat-exporter — agent-decrypt-guide.md](https://github.com/robbin/wechat-exporter/blob/main/references/agent-decrypt-guide.md) — SQLCipher page/HMAC/WAL spec
- [robbin/wechat-exporter — database-schema.md](https://github.com/robbin/wechat-exporter/blob/main/references/database-schema.md) — table/column reference
- [ydotdog/wechat-export-macos](https://github.com/ydotdog/wechat-export-macos) — macOS key extraction + decrypt
- [lyrcsjy/wechat-decrypt](https://github.com/lyrcsjy/wechat-decrypt) — cross-platform toolbox, `.dat` decrypt, 企业微信
- [jackwener/wx-cli-again](https://github.com/jackwener/wx-cli-again) — Apache-2.0 Rust CLI
- [tars1230/wechat-favorites-rescue](https://github.com/tars1230/wechat-favorites-rescue) — favorite.db (SQLCipher-4)
- [tqjason/WeChatMsg — hard_link.py](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py) — hardlink_info_v3 schema
- [sjzar/chatlog](https://github.com/sjzar/chatlog) — removal notice 2025-10-20
- [lc044/WeChatMsg](https://github.com/LC044/WeChatMsg) — 留痕, now readme-only
- [xaoyaoo/PyWxDump](https://github.com/xaoyaoo/PyWxDump) — takedown
- [LifeArchiveProject/WeChatDataAnalysis — media_helpers.py](https://github.com/LifeArchiveProject/WeChatDataAnalysis/blob/main/src/wechat_decrypt_tool/media_helpers.py) — attach bucketing

**Vendor / official**
- [企业微信 获取会话内容](https://developer.work.weixin.qq.com/document/path/91774) · [使用前帮助](https://developer.work.weixin.qq.com/document/path/91361) · [微盘 获取文件列表](https://developer.work.weixin.qq.com/document/path/93657)
- [腾讯文档开放平台 接口索引](https://docs.qq.com/open/document/saas/openapi/file/) · [MCP](https://docs.qq.com/open/document/saas/mcp.html) · [Skill](https://docs.qq.com/open/document/saas/skill.html)
- [pywecomarchive (PyPI)](https://pypi.org/project/pywecomarchive/) — macOS not supported
- [Apple — Folder Actions reference](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_folder_actions.html) · [Watching Folders](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/WatchFolders.html)

**Secondary**
- [Mole — 卸载微信前先备份聊天记录](https://mole.fit/zh/tested-apps/wechat) — WeChat 4 macOS path table, 3.x legacy path
- [IT之家 — 微信支持多人发送相同文件只存一次](https://m.ithome.com/html/813218.htm) — WeChat engineer on md5+size dedup and `(1)` suffixes
- [Pcdaily — 微信新揭秘：多人发同文件仅存一次](https://www.pcd.com.cn/redian/202411/21510.html)
- [V2EX 1167055](https://global.v2ex.co/t/1167055) — chatlog takedown and the fate of sibling projects
- [ClawHub — 微信附件整理](https://clawhub.ai/samjoeyang/skills/wechat-attachment-organizer) — msg/ layout, `.dat` decoding, sha256 dedup
- [CSDN — MAC版微信文件存储路径](https://blog.csdn.net/m0_63814634/article/details/148789158)
- [微信4.x数据库结构解析（一）总览与数据目录结构](http://mp.weixin.qq.com/s?__biz=MzYzOTk5OTQ5NA==&mid=2247483674&idx=1&sn=016c5617cb4b8dbfc6ab7de24e949812) ·
  [（六）媒体与附件（hardlink / media / .dat）](http://mp.weixin.qq.com/s?__biz=MzYzOTk5OTQ5NA==&mid=2247483706&idx=1&sn=bbfd6b0b9f00a5a6037290e514d88982)

---

## Open questions / UNVERIFIED

| Item | Status |
|---|---|
| WeChat 4.2.0 macOS key-shape branch (A raw-hex vs. B passphrase-derived) | **UNVERIFIED** — determine empirically; `rmqg` verified only against 4.1.11 |
| Whether `file_hardlink_info_v3` exists and is populated in macOS 4.2.0 `db_storage` | **UNVERIFIED** — schema references are Windows-shaped |
| Exact macOS per-conversation partition under `msg/file/` | **UNVERIFIED** — depends on the hardlink `dir1` mapping |
| WeChat 4.2.0 "auto-save received files to a folder" preference | **UNVERIFIED** — no such config key found in the binary; check Settings UI |
| Behaviour of the Save-to-Computer action under UI automation | **UNVERIFIED** — WeChat exposes read-only/protect toggles |
| WeCom local macOS storage path | **UNVERIFIED** — 企业微信 not installed here |
| Tencent C SDK availability for macOS (would remove the Windows/Linux collector need) | **UNVERIFIED** — no macOS wheel shipped by `pywecomarchive` |
| WeChat 3.x historical tree layout on this machine | **UNVERIFIED** — container unreadable without FDA; documented from Mole |
