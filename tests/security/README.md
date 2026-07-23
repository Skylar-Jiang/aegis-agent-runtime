# Security tests

Phase 3 的真实 `RuleBasedPreExecutionChecker` 复核请求与上游风险结论，真实
`RuleBasedPostExecutionChecker` 检查输出和所有待提交 artifact。测试不依赖 LLM。

成员 C 的下载 artifact 至少提供：

```json
{
  "type": "quarantined_download",
  "request_id": "request-001",
  "status": "QUARANTINED",
  "path": "request-001/payload.bin",
  "sha256": "<64 hex>",
  "size_bytes": 123,
  "content_type": "text/plain",
  "source_url": "https://example.com/log.txt",
  "final_url": "https://example.com/log.txt",
  "redirect_chain": [],
  "target_path": "downloads/log.txt"
}
```

成员 C 的 Memory artifact 至少提供：

```json
{
  "type": "pending_memory",
  "request_id": "request-001",
  "status": "PENDING",
  "key": "project-note",
  "content": "pending value"
}
```

Checker 允许额外审计字段，但会拒绝关联 ID、状态、路径、哈希、大小、URL、目标或
Memory 内容不一致的结果。下载 payload 必须真实存在于注入的 `quarantine_root`，文件写入
仍使用 PendingRecord v1 和注入的 `pending_root`。
