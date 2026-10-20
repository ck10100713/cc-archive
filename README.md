# CC Archive

把 `~/.claude/projects` 裡的 Claude Code 對話紀錄，變成一個可以瀏覽、搜尋的本機網站。特別適合回頭看 **`/compact` 到底留下了什麼**。

Turn your local Claude Code session logs into a browsable, searchable static site, with a dedicated view for compaction summaries.

## 功能

- **總覽**：session 數、prompt 數、活躍時數、compact 次數、輸出 tokens
- **近 30 天活動**：每天的 prompt 量，點某一天就篩出當天的 session
- **專案分類**：依資料夾分組，`.claude/worktrees/*` 自動併回原本的 repo
- **全文搜尋**：標題、每一則 prompt、compact 摘要、recap 都搜得到，命中處會高亮（快捷鍵 `/`）
- **篩選與排序**：7/30/90 天、有 compact、有 PR、是否包含 SDK session；可依最近、活躍時間、prompt 數、token 排序
- **Compact 時間軸**：每次 compact 是手動還是自動、前後 token 數、保留比例、摘要全文，還能一鍵跳回對話裡 compact 的位置
- **Session 詳細面板**：recap、compact、關聯 PR、prompt 時間軸
- **永久封存**：Claude Code 預設 30 天後會清掉舊 transcript，CC Archive 會留著，標成「已封存」
- **完整對話頁**：由 [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) 產生
- 深淺色主題、手機版版面

## 使用

需要先裝 [uv](https://docs.astral.sh/uv/)。

```bash
uv run build.py --open   # 第一次會全部產生，之後只重建有變動的 session
uv run build.py --full   # 強制全部重建
```

產出的網站在 `site/`，直接用瀏覽器打開 `site/index.html` 就能看。

### 自動更新

在 `~/.claude/settings.json` 加一個 SessionEnd hook，每次結束 session 就在背景重建（增量建置約 2 秒，有 file lock 防止重複執行）：

```json
{
  "hooks": {
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "cd \"$HOME/path/to/cc-archive\" && nohup uv run build.py > site/build.log 2>&1 &",
        "timeout": 10
      }]
    }]
  }
}
```

## 隱私

- 純靜態檔案：沒有 server、不連網、沒有追蹤。
- `site/` 裡就是你的完整對話內容，可能包含 token 或公司程式碼。已經寫進 `.gitignore`，**不要 commit、也不要上傳**。
- `site/` 的權限設成 `700`，只有你自己讀得到。

## 讀取哪些資料

Claude Code 寫進 JSONL 的這幾種紀錄：

| 紀錄 | 用途 |
|---|---|
| `custom-title` / `agent-name` / `ai-title` | session 標題 |
| `system/compact_boundary` | compact 觸發方式、前後 token 數 |
| `isCompactSummary` | compact 摘要本文 |
| `system/away_summary` | recap |
| `pr-link` | 關聯的 PR |
| `entrypoint` | 來源是 CLI、Desktop 或 SDK |

這些是 Claude Code 的內部格式，不是公開規格，之後版本有變動的話可能需要調整。

## License

MIT
