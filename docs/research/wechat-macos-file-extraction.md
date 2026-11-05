# Extracting Files from WeChat on macOS (微信 / WeChat 4.x)

Research report for the 052-DocIndex document-archiving system.
Target machine: macOS 27.0.1 (build 26A434), WeChat **4.2.0** (App Store build), container `com.tencent.xinWeChat`.

**Method note:** Findings marked **[verified locally]** were confirmed on the user's own machine by inspecting the installed
WeChat binary (`/Applications/WeChat.app/Contents/Resources/wechat.dylib` via `strings`), reading code
signing entitlements, and probing filesystem access. The rest are sourced from primary documentation and
source repositories, cited inline.

---

## 1. Where WeChat stores received files on macOS

### 1.1 The layout (WeChat 4.x)

Root of the 4.x data tree on macOS:

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
```

Below that, one directory per logged-in account. The account folder name carries a trailing hash,
e.g. `wxid_xxxxxxxxxxxxxxxx_<hash>`. The tool `rmqg/wechat-mac-export` discovers it two ways
([`wxexport/detect.py`](https://github.com/rmqg/wechat-mac-export/blob/main/wxexport/detect.py)):

- `xwechat_files/all_users/login/<wxid>/`
- `xwechat_files/<wxid>_<hash>/db_storage/`

Per-account structure:

| Path | Contents |
|---|---|
| `<account>/db_storage/` | Encrypted SQLite databases (messages, contacts, sessions, favorites) |
| `<account>/msg/attach/` | Images (and some media), bucketed by `md5(peer_username)` |
| `<account>/msg/video/` | Video messages |
| `<account>/msg/file/` | **Received documents/files — this is what a document archiver wants** |
| `<account>/hardlink/` | Hardlink index tables mapping md5 → stored file paths |
| `<account>/cache/` | Service caches (e.g. Moments) |
| `<account>/temp/head_image/` | Avatars |
| `xwechat_files/all_users/` | Cross-account data |

**This exact set of directory names was confirmed directly in the installed WeChat 4.2.0 binary [verified locally].**
Running `strings` over `wechat.dylib` yields the standalone path tokens `all_users`, `db_storage`, `message`,
`session`, `contact`, `favorite`, `login`, `attach`, `file`, `video`, `hardlink`, `cache`, `temp`.

Two independent secondary sources confirm the same table:

- Mole's WeChat 4 rules table maps `msg/file` → "收到的文件" (received files), `msg/attach` + `msg/video` →
  "按月份存放的聊天图片和视频" (monthly-partitioned chat images and videos), `db_storage` → "聊天数据库"
  ([mole.fit/zh/tested-apps/wechat](https://mole.fit/zh/tested-apps/wechat)).
- The `wechat-attachment-organizer` skill lists macOS 4.x as
  `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<账号>/msg/` with sources
  `msg/file`, `msg/attach`, and `.dat` images
  ([clawhub.ai/samjoeyang/skills/wechat-attachment-organizer](https://clawhub.ai/samjoeyang/skills/wechat-attachment-organizer)).

### 1.2 Answers to the specific sub-questions

**(a) Files received in chats** → `<account>/msg/file/`. On disk the file keeps its original name; the
per-conversation partition comes from the `hardlink` tables (see §3.3), which store `file_name` plus two
`dir2id.username` values.

**(b) "Chat Files" / 聊天文件** → The WeChat 4 UI has a dedicated Chat Files tab. The binary contains the UI
string `fatal pachat_files_page_{"chat_files_tabchat_files_page` and the localization "Manage all chat files in
\"Chat Files\"; Open chat files in read-only mode" [verified locally]. The Chat Files view is a **virtual view over
the same `msg/file` + `msg/attach` + `msg/video` storage**, not a separate store — i.e. 聊天文件 is a filtered UI
over the per-account `msg/` tree, not a distinct directory.

**(c) Images/videos cached** → `msg/attach/<md5(peer_username)>/<YYYY-MM>/…` and `msg/video/…`.
Mole documents these as monthly-partitioned. `media_helpers.py` in
[`LifeArchiveProject/WeChatDataAnalysis`](https://github.com/LifeArchiveProject/WeChatDataAnalysis/blob/main/src/wechat_decrypt_tool/media_helpers.py)
confirms the bucketing: `root / "msg" / "attach" / hashlib.md5(username.encode("utf-8")).hexdigest()`.
Image filenames are **not** the originals — they are `<file_name>.dat`, `<file_name>_t.dat` (thumb),
`<file_name>_h.dat`, `<file_name>_W.dat`, falling back to `<local_id>_<create_time>.dat`
([`tqjason/WeChatMsg` `wxManager/db_v4/hardlink.py`](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py)).

**(d) The message database** → `<account>/db_storage/`, encrypted SQLite. See §3.

### 1.3 A naming wrinkle worth knowing [verified locally]

The installed 4.2.0 binary contains **both** `xweChat_files` and `WeChat Files`, plus the migration prompt:

> `Chat history from previous versions detected in Weixin data directory. Rename "xweChat_files" to "WeChat Files" to continue.`

So 4.x has used more than one folder name across versions, and `WeChat Files` (the 3.x name) is being reused as
the 4.x name in some builds. **Do not hard-code the folder name.** Discover it instead — the method recommended by
[`rmqg/wechat-mac-export`](https://github.com/rmqg/wechat-mac-export/blob/main/wxexport/detect.py):

```python
glob.glob(os.path.join(data_dir, "*", "db_storage"))
```

and, generically, per the
[`agent-decrypt-guide.md`](https://github.com/robbin/wechat-exporter/blob/main/references/agent-decrypt-guide.md):

```bash
find ~/Library/Containers -maxdepth 6 -type d -name db_storage 2>/dev/null
```

Also note WeChat 3.x stored data in a completely different place:
`~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/<version>/<account>/`
with media in `Message/MessageTemp` ([mole.fit](https://mole.fit/zh/tested-apps/wechat)). If the archive must also
cover historical 3.x data, that tree needs a separate migration step.

---

## 2. Can files be extracted non-interactively?

### 2.1 There is no supported CLI

**WeChat for Mac exposes no documented or supported command-line interface for exporting files or message data.**
No official automation surface exists. Everything below is either (a) reading the on-disk container directly, or
(b) driving the GUI.

### 2.2 The sandbox question — answered with a local experiment

The container is protected. Confirmed on the user's machine [verified locally]:

**WeChat is App Sandboxed** (`codesign -d --entitlements - /Applications/WeChat.app`):

```
[Key] com.apple.security.app-sandbox
[Value] [Bool] true
[Key] com.apple.security.application-groups
[Value] [Array] [String] 5A4RE8SF68.com.tencent.xinWeChat
[Key] com.apple.security.files.user-selected.read-write
[Value] [Bool] true
[Key] com.apple.security.files.downloads.read-write
[Value] [Bool] true
```

**But the block on our own process is macOS TCC, not POSIX permissions.** The precise behaviour observed:

```
os.stat('.../Data')            → SUCCEEDS  (metadata readable)
os.stat('.../Data/Documents')  → SUCCEEDS  (metadata readable)
os.listdir('.../Data/Documents') → FAILS with OSError errno=1 EPERM
open('.../Data/Documents/xwechat_files') → FAILS with EPERM
```

This is the signature of **TCC protection of "App Data" / container contents** on modern macOS. Because the
directories are `drwx------` owned by uid 501 (the user), a *plain* POSIX permission check would have allowed
access — the `EPERM` on `open`/`listdir` while `stat` succeeds is TCC refusing the read. macOS 27 applies this
to app containers broadly, not only to the app that owns them.

**Practical consequence:** a third-party script **cannot** read
`~/Library/Containers/com.tencent.xinWeChat/Data/` without either:
- **Full Disk Access** granted to the executing binary (Terminal/iTerm/Python/your agent's host app), or
- the WeChat app's own sandbox profile (i.e. code running *as* WeChat).

Note that granting Full Disk Access to a Python interpreter or to a terminal is broad and may be undesirable in a
managed archive pipeline. This is the single biggest practical constraint on the whole problem.

### 2.3 What actually works without Full Disk Access

Two routes sidestep the container entirely:

1. **Drive the GUI** — WeChat's own process reads its own container, so it can save files out. Use the
   「保存到电脑」 / Save-to-Computer action, or AppleScript/UI automation of the Chat Files window, writing into a
   folder outside the container.
2. **Use the sandbox profile explicitly** — WeChat holds
   `com.apple.security.files.user-selected.read-write` and
   `com.apple.security.files.downloads.read-write`, so files it writes to `~/Downloads` or a user-selected folder
   are reachable by ordinary processes without FDA.

The File-System/automation permissions the user grants to WeChat (via the file-picker) are the cleanest
mechanism: WeChat writes *out* of its container, and the archiver reads from a plain folder.

---

## 3. The message database

### 3.1 Location and format

Databases live in `<account>/db_storage/`, as `<subdir>/<name>.db` with sibling `-wal` and `-shm` files.
The subdirectories are `message`, `session`, `contact`, `favorite`, `login`, and others [verified locally].

**Encryption: yes.** WeChat uses **WCDB (SQLCipher 4)**, AES-256-CBC, per-database key.
[`ydotdog/wechat-export-macos`](https://github.com/ydotdog/wechat-export-macos) states it plainly:

> 微信使用 WCDB（基于 SQLCipher 4）加密本地数据库。每个 `.db` 文件有独立的 AES-256 密钥，缓存在进程内存中，
> 格式为 `x'<64hex_key><32hex_salt>'`。

The exact on-disk page format is fully specified in
[`robbin/wechat-exporter` → `references/agent-decrypt-guide.md`](https://github.com/robbin/wechat-exporter/blob/main/references/agent-decrypt-guide.md),
which is the most rigorous primary spec available:

| Item | Value |
|---|---|
| Page size | 4096 bytes |
| Cipher | AES-256-CBC |
| Key length | 32 bytes (`enc_key`) |
| Per-page IV | 16 bytes |
| Per-page auth | HMAC-SHA512, 64 bytes |
| Per-page reserve | 80 bytes = IV(16) + HMAC(64) |
| Usable per page | 4016 bytes |

Layout per page (page 1 differs):

| Page | salt | ciphertext | IV | HMAC |
|---|---|---|---|---|
| Page 1 | `[0,16)` | `[16,4016)` | `[4016,4032)` | `[4032,4096)` |
| Page 2+ | — | `[0,4016)` | `[4016,4032)` | `[4032,4096)` |

Key derivation and page authentication:

```
mac_salt = salt each byte XOR 0x3a
mac_key  = PBKDF2-HMAC-SHA512(enc_key, mac_salt, iterations=2, dkLen=32)

start  = 16 if page_no == 1 else 0      # page 1's salt is NOT authenticated
input  = page[start:4032] || page_no (4-byte little-endian)
value  = HMAC-SHA512(mac_key, input)
```

Page 1 decrypted output is reconstructed as
`b"SQLite format 3\x00" + plain + b"\x00" * 80`.

**Two key-shape branches exist (version-dependent — do not guess from the version number):**

| Branch | Versions | Key source |
|---|---|---|
| **A** | 4.0.x and some 4.1.x | Raw ASCII `x'<64 hex><32 hex>'` present in process memory; first 64 hex = `enc_key`, last 32 hex = salt |
| **B** | reported from 4.1.10 onward | Only a **passphrase** is cached; `enc_key = PBKDF2-HMAC-SHA512(passphrase, salt, 256000, 32)` |

The guide is explicit that branch detection is **empirical**: scan as branch A and validate candidates against
page-1 HMAC; only if nothing matches, consider branch B. A successful scan of zero candidates also has to be
distinguished from insufficient permissions or a target DB the client has not yet opened.

> **Note for 4.2.0:** the branch boundary is reported as "4.1.10 onward" for branch B, and
> `rmqg/wechat-mac-export` states it is built and verified against **WeChat 4.1.11**, where "the memory-scanning
> approach used by older tools no longer works" — it therefore uses an `lldb` breakpoint on Apple's CommonCrypto
> instead. **Whether WeChat 4.2.0 on macOS 27 behaves as A or B is unverified here** and must be determined
> experimentally on this machine.

### 3.2 Message tables and columns

Per the `agent-decrypt-guide` schema reference
([`references/database-schema.md`](https://github.com/robbin/wechat-exporter/blob/main/references/database-schema.md))
and confirmed by `rmqg/wechat-mac-export`'s working exporter:

- **Sharding:** `db_storage/message/message_0.db`, `message_1.db`, … Each shard may hold **multiple conversation
  tables**. Must scan *all* shards, including multi-digit shard numbers — a new conversation can land in a
  higher-numbered shard.
- **Conversation table name:** `Msg_<md5(username)>` where `<md5>` is the **MD5 of the peer's `username`**
  (for group chats, `username` ends in `@chatroom`).
- **Mapping table:** `Name2Id` with column `user_name` (plus `rowid`).

```python
import hashlib, sqlite3
conn = sqlite3.connect("file:message_0.db?mode=ro", uri=True)
for (username,) in conn.execute("SELECT user_name FROM Name2Id"):
    table = "Msg_" + hashlib.md5(username.encode()).hexdigest()
    # check the table actually exists before querying
```

**Message columns** (verified in the installed binary [verified locally] — `local_type`, `message_content`,
`real_sender_id`, `create_time`, `user_name`, `FileName`, `Reserved` all appear as literals in `wechat.dylib`):

| Column | Meaning |
|---|---|
| `local_type` | Message type (1=text, 3=image, 34=voice, 43/63=video, 47=sticker, 49=app/file/link, 10000=system) |
| `message_content` | Payload. Plain text for type 1; **XML** for media/app messages. Prefix form: `sender_wxid:\n<payload>` |
| `real_sender_id` | FK into `Name2Id.rowid` for the sender (needed for group chats) |
| `create_time` | Unix timestamp (seconds) |
| `msg_id` | Message id (present in the binary) |

**Important detail:** some BLOBs are **zstd-compressed** — rows flagged `WCDB_CT_*` carry the magic
`28 b5 2f fd` and must be zstd-decompressed before UTF-8 decoding. The `rmqg/wechat-mac-export` exporter does this
in `_bdecode()` and is a good reference implementation.

**Type 49 (app/file messages) XML** contains the attachment metadata. From `tqjason/WeChatMsg`
[`hard_link.py`](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py):

```python
root.find(".//img").get("md5")        # image
root.find(".//videomsg").get("md5")   # video
```

The XML also carries `<title>`, `<des>`, and for files the filename/attributes.

### 3.3 The hardlink tables (the attachment index)

This is the key to mapping a message → the actual file on disk. `tqjason/WeChatMsg` queries them as:

```sql
select file_size, type, file_name, dir2id.username, dir2id2.username, _rowid_, modify_time, extra_buffer
from file_hardlink_info_v3
join dir2id on dir2id.rowid = dir1
LEFT JOIN dir2id AS dir2id2 ON dir2id2.rowid = dir2 AND dir2 != 0
where md5=?
```

Tables: **`image_hardlink_info_v3`**, **`video_hardlink_info_v3`**, **`file_hardlink_info_v3`** — each with an
`md5` column (indexed). `dir2id` maps directory rowids → usernames.

Path reconstruction from `hard_link.py`:

```python
image_root_path = "msg/attach/"
video_root_path = "msg/video/"
file_root_path  = "msg/file/"

# files (type != 6):
filepath = os.path.join(file_root_path, dir1, file_name)
# where dir1 = dir2id.username (the peer's username)

# videos (type == 5):
filepath = os.path.join(video_root_path, dir1, dir2, 'Rec', dir3, 'V', file_name)
# where dir3 comes from the protobuf in extra_buffer
```

**Note:** `file_hardlink_info_v3` is a *Windows*-shaped schema (backslash roots, `dir1`/`dir2`/`dir3` columns).
On macOS the same logical tables exist but the layout may differ; `rmqg/wechat-mac-export` (macOS-native)
reads only `message/message_*.db` and does **not** use the hardlink tables. **Unverified on macOS:** whether
`file_hardlink_info_v3` exists and is populated in the macOS 4.2.0 `db_storage`. This must be checked against the
actual decrypted DBs before relying on it.

---

## 4. Third-party tools

### 4.1 ⚠️ The critical finding: the popular tools have been removed

**`chatlog` (`sjzar/chatlog`) was deleted in October 2025 after receiving a legal letter from WeChat (微信官方).**
The repo now contains only a removal notice ([github.com/sjzar/chatlog](https://github.com/sjzar/chatlog)):

> 我收到了来自微信官方的函件，明确项目的核心功能存在合规风险。为了彻底规避法律问题，并本着对开源社区负责任的态度，
> 我决定 **移除本项目的全部代码与提交历史记录。**

Previously it was 9.1k★, Go, MIT-licensed. The code is recoverable from the GitHub *fork network* — community members
documented fetching the last commit via `git fetch origin <sha> && git reset --hard <sha>` before the push-to-delete
([V2EX thread 1167055](https://global.v2ex.co/t/1167055)). Forks still exist (e.g.
[`Symb0x76/chatlog`](https://github.com/Symb0x76/chatlog), Apache-2.0, 70★, now archived). **The V2EX thread
also notes the same fate for MemoTrace (留痕), WeChatBackup, and PyWxDump** — "都是一样的命运，没一个逃过
'南山必胜客'的制裁."

**This is a material risk for a production archiving system:** the tool category is under active legal pressure from
the vendor. Any solution built on DB-decryption carries real legal risk, not just technical risk.

### 4.2 Tool-by-tool status

| Tool | Lang | License | Platform | Status | Notes |
|---|---|---|---|---|---|
| [`rmqg/wechat-mac-export`](https://github.com/rmqg/wechat-mac-export) | Python | GPL-3.0 / MIT | **macOS only**, WeChat 4.1.x | Active (pushed 2026-09) | **Most macOS-specific.** Uses `lldb` on CommonCrypto instead of memory scanning, because memory scanning broke on 4.1.11. Requires SIP off + re-signed WeChat + `sudo`. Exports HTML/CSV/text. |
| [`ydotdog/wechat-export-macos`](https://github.com/ydotdog/wechat-export-macos) | Python + C | WTFPL | **macOS** (Apple Silicon & Intel), WeChat 4.x | Active (2026-04) | Mach VM memory scan for `x'<64hex><32hex>'` keys, AES-256-CBC per-page decrypt, export TXT/CSV/JSON. Documents the re-signing rationale. |
| [`robbin/wechat-exporter`](https://github.com/robbin/wechat-exporter) | Spec (Markdown) | — | **macOS** WeChat 4.x | Active (2026-10) | **Not a program — a rigorous implementation spec.** The `agent-decrypt-guide.md` is the best public documentation of the page format, HMAC, WAL handling and acceptance criteria. Explicitly says its old upstream (`cocohahaha/wechat-decrypt-macos`) is gone. |
| [`lyrcsjy/wechat-decrypt`](https://github.com/lyrcsjy/wechat-decrypt) | Python | none stated | Windows / **macOS** / Linux | Active (2026-07) | Full toolbox: DB decrypt, image `.dat` decrypt (V1/V2/wxgf), Moments, SILK→WAV→ASR, Web UI, MCP server, live message monitor. macOS path documented. Also 企业微信 5.x (wxSQLite3 AES-128). |
| [`jackwener/wx-cli-again`](https://github.com/jackwener/wx-cli-again) | Rust | **Apache-2.0** | macOS / Linux / Windows | Active (2026-09) | **"fresh start from wx-cli"** — a post-takedown successor to `chatlog` in spirit. Single Rust binary, background daemon caching decrypted DBs, `attachments` command. ⚠️ README states the source repo `botiverse/wx-cli` is now **private**; the public repo is a "fresh start". |
| [`tars1230/wechat-favorites-rescue`](https://github.com/tars1230/wechat-favorites-rescue) | Python | MIT | Windows primary, **macOS partial** | Active (2026-08) | Decrypts `favorite.db` (SQLCipher-4) → exports 收藏 (Favorites) as a knowledge base. Depends on external `wechat-decrypt` + `wechat-mcp-server` components. |
| [`Thearas/wechat-db-decrypt-macos`](https://github.com/Thearas/wechat-db-decrypt-macos) | C | none | macOS arm64, WeChat 4.1.2.241 | 2026-07 | README is now just "本人遵纪守法，先溜了" — **author has withdrawn.** |
| [`328336690/wechat-decrypt`](https://github.com/328336690/wechat-decrypt) | — | MIT | Windows (4.0) | 2026-06 | Memory-based key extraction, Windows-oriented. |
| [`shaoguangfeng/wechat-decrypt` / `cocohahaha/wechat-decrypt-macos`](https://github.com/robbin/wechat-exporter/issues/1) | — | — | — | **Unavailable** | Upstreams referenced by surviving projects have gone away. |
| [`lc044/WeChatMsg`](https://github.com/LC044/WeChatMsg) (留痕/MemoTrace) | Python | — | Windows | **Effectively dead** | Repo now holds only `readme.md` + `doc/`; README says "此项目很久没有（也不会）更新了". 42k★ but no code. |
| [`MetaZhi/MemoTrace`](https://github.com/MetaZhi/MemoTrace) | — | MIT | — | 2025-04, 5★ | Mirror/ghost of MemoTrace. |
| [`xaoyaoo/PyWxDump`](https://github.com/xaoyaoo/PyWxDump) | Python | — | Windows | Takedown / mirror | Reported as also deleted; forks preserve code. |
| [`Bryan-Cyf/WeChatDaily`](https://github.com/Bryan-Cyf/WeChatDaily) | Python | MIT | — | 2026-07 | AI-agent skill + daily group report generator; bundles `tools/wechat-decrypt`. |
| [`sqjin/WeChatMsg` / `tqjason/WeChatMsg`](https://github.com/tqjason/WeChatMsg) | Python | — | Windows (code paths use `msg\\`) | Source available | **Valuable as a schema reference** even though Windows-shaped — best documented source for the `hardlink_info_v3` tables and message XML structure. |

### 4.3 Recommendation

For a **document-archiving** system (files, not full chat history), the best current combination is:

1. **`ydotdog/wechat-export-macos` (WTFPL)** or **`rmqg/wechat-mac-export` (GPL-3.0)** for the decryption path if
   message↔file association via DB is required. Note `rmqg` is GPL-3.0 — **copyleft**: if its code ends up in a
   distributed product, obligations attach. `ydotdog`'s WTFPL is permissive but non-standard; verify terms.
2. **`robbin/wechat-exporter`'s `agent-decrypt-guide.md`** as the authoritative spec to validate any
   implementation, and to avoid the documented pitfalls (page-1 salt offset, WAL checkpoint return-value
   interpretation, salt-vs-path key mapping).
3. **`jackwener/wx-cli-again` (Apache-2.0)** is the cleanest license if a maintained CLI is needed — but verify
   the actual source availability, since the README says the upstream repo is private.

**Licence caveat:** everything in this category is best treated as *spare parts for your own private tooling*, not
as a dependency to vendor into a shipped product, given the GPL/WTFPL mix and the demonstrated legal pressure.

---

## 5. Practical alternative approaches

Ordered by realism for a working archive pipeline on this machine.

### (a) 【Recommended】GUI-driven save into a watched folder — no sandbox fight, no legal risk

WeChat's own process reads its own container, so it can write files out. Use 「保存到电脑」 / Save-to-Computer,
or AppleScript-driven UI automation over the Chat Files window, to land files in a plain folder such as
`~/Documents/WeChatInbox/`. That folder is outside the container and needs **no Full Disk Access**.

Then let the archiver watch that folder. This is the only approach that is simultaneously:
- technically reliable on macOS 27,
- free of TCC friction,
- free of DB-decryption legal exposure,
- and free of the WeChat-update brittleness (nothing breaks when 4.2 → 4.3 changes the DB format).

**Cost:** it is manual or GUI-automated — not a fire-and-forget headless ingest.

### (b) Folder Actions / Shortcuts automation

macOS's own mechanisms to move/rename/copy files as they land:
- **Folder Actions** — attach a script to a folder via Finder → Services → Folder Actions. Apple's
  [Folder Actions AppleScript reference](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_folder_actions.html)
  and [Mac Automation Scripting Guide: Watching Folders](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/WatchFolders.html).
- **Shortcuts** — a Shortcut can watch a folder (or be triggered by a file-add event) and run a shell script.

These watch a **non-container** folder, so they inherit none of the TCC problem. They are the natural complement
to (a): WeChat saves into `~/Documents/WeChatInbox/`, a Folder Action or Shortcut normalizes names, computes
hashes, and moves files into the archive.

### (c) Direct DB parsing — powerful, brittle, legally sensitive

If message↔file association and sender/date metadata are needed, decrypt `db_storage/message/message_*.db` and
`file_hardlink_info_v3`, then join `Msg_<md5(username)>` → `Name2Id` → hardlink → file path. Requirements:
- **Full Disk Access** for the decrypting process (to read the container), *or* a copy made by a process that
  already has access.
- **Re-signed WeChat** (`sudo codesign --force --deep --sign - /Applications/WeChat.app`) to strip Hardened
  Runtime, **or** SIP disabled, to permit `task_for_pid` memory reads. Re-signing breaks the app's signature
  identity, so Keychain items, granted system permissions (mic/camera/screen recording), and auto-update may all
  require re-authorisation. It must be redone after every WeChat update.
- Branch A vs B key-shape determination (see §3.1) — **4.2.0 unverified**.
- Correct WAL handling — nearly every `.db` has a sibling `-wal`; ignoring it silently loses the newest
  transactions. `agent-decrypt-guide.md` §8.5 gives the correct SQLCipher-based procedure:

  ```sql
  PRAGMA key = "x'[enc_key][salt]'";
  PRAGMA cipher_compatibility = 4; PRAGMA cipher_page_size = 4096;
  PRAGMA wal_checkpoint(PASSIVE);
  ```
  and the acceptance rule: success requires `busy == 0` **and** `log == checkpointed`. `busy == 0` alone is
  insufficient — a reader can block backfill while still reporting `busy=0` (observed `(0,5,3)`).

**Verdict: viable for a one-off historical backfill, poor as a standing ingestion pipeline.**

### (d) 企业微信 / WeCom — the officially supported route (strong recommendation if available)

WeCom has a **first-party, sanctioned API for archiving chat content**: **会话内容存档 (Conversation Content
Archive)**. This is not a workaround — it is the vendor's intended mechanism, and it avoids all container/TCC
decryption entirely.

- Official docs: [获取会话内容](https://developer.work.weixin.qq.com/document/path/91774) ·
  [使用前帮助](https://developer.work.weixin.qq.com/document/path/91361)
- Requires: an enterprise admin to enable the feature, a **Corpid**, an archive **Secret**, the enterprise **RSA
  private key** for message decryption, and the calling IP registered as a trusted IP in the admin console.
- Flow: `get_chat_data(seq, limit)` → encrypted records → decrypt `encrypt_random_key` with the RSA private key →
  `decrypt_data(encrypt_key, encrypt_chat_msg)` → typed message models → **download media by `sdkfileid`** via
  chunked `iter_media_data(sdkfileid)` (each chunk returns `data`, `indexbuf`, `is_finish`).
- Batch limit is 1000 records per call; persist the last processed `seq` to resume.

**Key limitation — and it is a real one for this machine:**
[`pywecomarchive`](https://pypi.org/project/pywecomarchive/) (MIT, released 2026-10-28) — a Python wrapper over
the official native SDK — **explicitly does not support macOS**:

> 暂不支持 macOS、Windows ARM64、32 位系统和 Alpine Linux / musl。

Supported: Windows x64, Linux glibc x86_64, Linux ARM64. So on macOS you would need to either run the collector
on a Windows/Linux host, or drive the official C SDK yourself via its own macOS build if Tencent ships one.
There is also [`wecomarchive`](https://pypi.org/project/wecomarchive/0.2.4/) and
[`pywecomarchive` on GitHub](https://github.com/911061873/pyWecomArchive).

**WeCom 微盘 (Drive) API** also exists — [获取文件列表](https://developer.work.weixin.qq.com/document/path/93657) —
for files in the enterprise drive, which is a cleaner ingest path than any local file scraping.

**Status on the user's machine: 企业微信 is NOT installed** [verified locally] — no `/Applications/企业微信.app`,
no `~/Library/Containers/com.tencent.WeWorkMac`. If the documents that need archiving arrive via WeCom rather than
WeChat, this changes the recommendation from "hard" to "easy", but requires a Windows/Linux collector host.

### (e) 腾讯文档 (Tencent Docs) — official export API

Tencent Docs has a full **Open API** for the SaaS product ([接口索引](https://docs.qq.com/open/document/saas/openapi/file/)):

- File APIs: `files:list`, `files:search`, `files:create`, `files/{fileId}` get/update/delete, `files/{fileId}:copy`,
  `files/{fileId}:move`, `files/{fileId}:recover`
- Async task APIs: `files/{fileId}:export` (**导出文件**), `files:upload`, `files:import`, `tasks/{taskId}` poll,
  `tasks/{taskId}:cancel`, `tasks/{taskId}:convertAndExportPDF` (**DOC 转 PDF**)

So a document living in 腾讯文档 can be exported programmatically with the vendor's blessing — a far better route
than scraping the WeChat container for a link to it. Note the docs also expose an [MCP interface](https://docs.qq.com/open/document/saas/mcp.html)
and a [Skill](https://docs.qq.com/open/document/saas/skill.html) for 腾讯文档 SaaS.

### (f) 收藏 (Favorites)

Favorites are stored in a **local SQLCipher-4 database `favorite.db`** under `db_storage` — the binary contains the
literal `favorite` directory token [verified locally], and
[`wechat-favorites-rescue`](https://github.com/tars1230/wechat-favorites-rescue) confirms `favorite.db` is
SQLCipher-4 encrypted.

- **There is no official export API for Favorites.** The rescue tool describes it as
  "官方没有导出按钮" (no official export button).
- Favorite `type` codes (from that tool): `1`=chat plain text, `2`=file, `3`=voice, `5`=公众号文章,
  `8`=mini-program, `14`=link/chat record, `18`=视频号.
- Requires the same key-extraction machinery as §3 — so the same TCC + re-signing constraints.
- The tool's own platform note: "**Windows**：完整支持（主力环境）; **macOS**：脚本已跨平台化（自动探测容器路径），
  但需 Mac 微信登录产生数据". It also depends on external `wechat-decrypt` and `wechat-mcp-server` components
  that are separately unavailable.

### Summary table

| Approach | macOS 27 viability | TCC/FDA needed | Legal risk | Maintenance burden | Fidelity |
|---|---|---|---|---|---|
| (a) GUI save → watched folder | ✅ Best | ❌ No | ✅ None | Medium (manual/UI automation) | Files only, no metadata |
| (b) Folder Actions / Shortcuts | ✅ Best | ❌ No | ✅ None | Low | Files only |
| (c) Direct DB parsing | ⚠️ Fragile | ✅ Yes + re-sign + SIP | ⚠️ Real (WeChat legal action precedent) | High (breaks on updates) | Full metadata |
| (d) 企业微信 会话内容存档 | ✅ Best **if WeCom is in play** | ❌ No | ✅ None (official API) | Low-medium | Full, official |
| (e) 腾讯文档 Open API | ✅ Best | ❌ No | ✅ None (official API) | Low | Full, official |
| (f) 收藏 / favorite.db | ⚠️ Fragile | ✅ Yes | ⚠️ Moderate | High | Favorites only |

---

## 6. Naming and dedup signals

### 6.1 Do received files keep their original filenames? — Yes, with a wrinkle

`file_hardlink_info_v3` stores a **`file_name`** column holding the original filename
([`tqjason/WeChatMsg` `hard_link.py`](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py)),
and the on-disk path is built as `msg/file/<dir1>/<file_name>`.

**However, the `(1)` / `(2)` suffix semantics are the opposite of what they look like.** Per a Tencent WXG
(微信事业群) engineer, 客村小蒋, speaking about the **desktop and mobile clients**:

> 电脑端和手机端收到同一份文件多次转发，**都只会保存一次**，文件后缀加（1）、（2）只是为了避免冲突，
> **并不是真实存储了多次**。
> 检测是否是同一个文件的方法，**是校验文件大小和 md5 值**。

([IT之家, 2024-11-26](https://m.ithome.com/html/813218.htm); also
[Pcdaily](https://www.pcd.com.cn/redian/202411/21510.html))

So:
- The `(1)` suffix is a **display-level collision avoidance** marker, **not** evidence of a second stored copy.
- The file is stored **once**.
- **Dedup key = (file size, MD5).** WeChat itself compares exactly these two.
- If the *format* changes (e.g. a video gains subtitles), WeChat treats it as a **different file** and stores it again.

**Corroborating evidence from the binary [verified locally]:** `wechat.dylib` contains the literals `filemd5`,
`filedatamd5`, `extern_md5`, `blockmd5`, `FileName`, `fileNamemimeTypedecryptKfileMd5`, plus the error strings
`FileMd5NotEqual` and `FileDataMd5NotMatch` — i.e. the client itself maintains MD5-based file identity and
compares them.

### 6.2 Is there a stable hash or msg id for dedup?

**Yes — several, at different levels:**

| Signal | Where | Stability | Use for |
|---|---|---|---|
| **File MD5** | `file_hardlink_info_v3.md5`, `image_hardlink_info_v3.md5`, `video_hardlink_info_v3.md5`; also in message XML `<img md5=…>`, `<videomsg md5=…>` | Stable per file content across chats and devices; changes if the file is re-encoded | **Primary dedup key** (with size) — matches WeChat's own rule |
| **File size** | `file_hardlink_info_v3.file_size` | Stable | Secondary check, pairs with MD5 |
| **`msg_id`** | message table | Unique per message instance | Message-level dedup / idempotent re-ingest |
| **`create_time`** | message table | Per message | Ordering, incremental watermark |
| **`_rowid`** / `modify_time` | hardlink tables | Per row | Change detection |
| **Conversation id** | `md5(username)` → `Msg_<md5>` table | Stable | Partitioning |
| **SHA-256 of bytes** | computed by you after extraction | Stable | **Recommended** for the archive's own dedup layer |

### 6.3 Practical dedup recommendation

Do **not** dedup on filename — `(1)` suffixes make it unreliable, and different files can share a name.

Instead, two-layer:

1. **WeChat-native layer:** dedup on `(file_size, md5)` from `file_hardlink_info_v3`. This reproduces WeChat's own
   semantics exactly and naturally collapses the same file received in N chats to one copy.
2. **Archive layer:** after extraction, compute **SHA-256** on the actual bytes and dedup on that. This survives
   re-encoding, catches corruption, and gives a content-addressed store. The
   `wechat-attachment-organizer` skill takes exactly this approach — "**仅当两个文件哈希值相同才视为重复，绝不比较文件名**"
   ([ClawHub](https://clawhub.ai/samjoeyang/skills/wechat-attachment-organizer)).

Keep a manifest keyed by `(md5, size)` → `(sha256, canonical path, first_seen_msg_id)` so that re-running the
ingest is idempotent and you can trace every archived file back to the message that introduced it.

---

## 7. Recommended plan for 052-DocIndex

1. **Ship (a) + (b) first.** Configure WeChat to save received documents into a plain folder outside the
   container (`~/Documents/WeChatInbox/`). Attach a Folder Action or Shortcut that computes SHA-256, normalizes
   names, and moves/links into the DocIndex store with a manifest. This is unimpeded by TCC, unaffected by
   WeChat updates, and carries no legal exposure.
2. **Add 腾讯文档 Open API (e)** for anything that is a 腾讯文档 link. Official, clean, and gives you metadata
   WeChat's container cannot.
3. **Evaluate 企业微信 会话内容存档 (d)** if the archive must also cover WeCom traffic — it is the only route that is
   both complete and sanctioned. Budget for a Windows/Linux collector host because the Python SDK does not ship
   macOS binaries.
4. **Keep DB parsing (c) as an offline backfill, not a live pipeline.** If historical metadata is needed, use
   `robbin/wechat-exporter`'s `agent-decrypt-guide.md` as the spec, `ydotdog/wechat-export-macos` or
   `rmqg/wechat-mac-export` as the implementation, and respect the WAL and branch-A/B caveats. Note the
   GPL-3.0 license of `rmqg` and the vendor's demonstrated legal pressure on this tool category.
5. **Do the dedup at two layers:** WeChat-native `(size, md5)` from the hardlink tables, plus SHA-256 over
   extracted bytes. Never dedup on filename.

---

## Sources

**Primary / local**
- WeChat 4.2.0 binary `/Applications/WeChat.app/Contents/Resources/wechat.dylib` — `strings` extraction of
  directory tokens and DB column names [verified locally]
- `codesign -d --entitlements - /Applications/WeChat.app` — App Sandbox entitlements [verified locally]
- Filesystem probe of `~/Library/Containers/com.tencent.xinWeChat/Data/` — `stat` succeeds, `listdir`/`open` EPERM [verified locally]
- macOS 27.0.1 (26A434); WeChat 4.2.0 [verified locally]

**Repository / specification**
- [rmqg/wechat-mac-export](https://github.com/rmqg/wechat-mac-export) — macOS WeChat 4.x decrypt/export
- [robbin/wechat-exporter — agent-decrypt-guide.md](https://github.com/robbin/wechat-exporter/blob/main/references/agent-decrypt-guide.md) — SQLCipher page/HMAC/WAL spec
- [robbin/wechat-exporter — database-schema.md](https://github.com/robbin/wechat-exporter/blob/main/references/database-schema.md) — table/column reference
- [ydotdog/wechat-export-macos](https://github.com/ydotdog/wechat-export-macos) — macOS key extraction + decrypt
- [lyrcsjy/wechat-decrypt](https://github.com/lyrcsjy/wechat-decrypt) — cross-platform toolbox, image .dat decrypt
- [jackwener/wx-cli-again](https://github.com/jackwener/wx-cli-again) — Apache-2.0 Rust CLI
- [tars1230/wechat-favorites-rescue](https://github.com/tars1230/wechat-favorites-rescue) — favorite.db (SQLCipher-4)
- [tqjason/WeChatMsg — hard_link.py](https://github.com/tqjason/WeChatMsg/blob/master/wxManager/db_v4/hardlink.py) — hardlink_info_v3 schema
- [sjzar/chatlog](https://github.com/sjzar/chatlog) — removal notice (2025-10-20)
- [lc044/WeChatMsg](https://github.com/LC044/WeChatMsg) — 留痕, now readme-only
- [xaoyaoo/PyWxDump](https://github.com/xaoyaoo/PyWxDump) — takedown
- [LifeArchiveProject/WeChatDataAnalysis — media_helpers.py](https://github.com/LifeArchiveProject/WeChatDataAnalysis/blob/main/src/wechat_decrypt_tool/media_helpers.py) — attach bucketing

**Vendor / official**
- [企业微信 获取会话内容](https://developer.work.weixin.qq.com/document/path/91774) · [使用前帮助](https://developer.work.weixin.qq.com/document/path/91361) · [微盘 获取文件列表](https://developer.work.weixin.qq.com/document/path/93657)
- [腾讯文档开放平台 接口索引](https://docs.qq.com/open/document/saas/openapi/file/) · [MCP](https://docs.qq.com/open/document/saas/mcp.html)
- [pywecomarchive (PyPI)](https://pypi.org/project/pywecomarchive/) — macOS not supported
- [Apple — Folder Actions reference](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_folder_actions.html) · [Watching Folders](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/WatchFolders.html)

**Secondary**
- [Mole — 卸载微信前先备份聊天记录](https://mole.fit/zh/tested-apps/wechat) — WeChat 4 macOS path table
- [IT之家 — 微信支持多人发送相同文件只存一次](https://m.ithome.com/html/813218.htm) — WeChat engineer on md5+size dedup and `(1)` suffixes
- [V2EX 1167055](https://global.v2ex.co/t/1167055) — chatlog takedown and the fate of sibling projects
- [ClawHub — 微信附件整理](https://clawhub.ai/samjoeyang/skills/wechat-attachment-organizer) — msg/ layout, .dat decoding, sha256 dedup
- [CSDN — MAC版微信文件存储路径](https://blog.csdn.net/m0_63814634/article/details/148789158)
- [微信4.x数据库结构解析（一）总览与数据目录结构](http://mp.weixin.qq.com/s?__biz=MzYzOTk5OTQ5NA==&mid=2247483674&idx=1&sn=016c5617cb4b8dbfc6ab7de24e949812) ·
  [（六）媒体与附件（hardlink / media / .dat）](http://mp.weixin.qq.com/s?__biz=MzYzOTk5OTQ5NA==&mid=2247483706&idx=1&sn=bbfd6b0b9f00a5a6037290e514d88982)
