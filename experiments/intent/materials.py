"""Build current-version report and blank human review/feedback records."""
import csv
import hashlib
import json
from pathlib import Path

from .dataset import ROOT, load
from .evidence import verify
from .freeze import fingerprints


def fraction(value):
    if value["value"] is None:
        return "不适用"
    return f"{value['numerator']}/{value['denominator']} ({value['value']:.1%})"


def main():
    output = ROOT / "materials"
    output.mkdir(exist_ok=True)
    cases = load()
    review_path = output / "review.csv"
    if not review_path.exists():
        create_review(review_path, cases)
    feedback_path = output / "feedback.csv"
    if not feedback_path.exists():
        with feedback_path.open("x", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerow(["feedback_id", "case_id", "label", "source", "reviewer",
                                        "old_policy_version", "candidate_version", "evaluation_ref",
                                        "approval", "approved_by", "rollback_ref"])
    current = fingerprints()
    matching, obsolete = [], []
    for folder in sorted((ROOT / "results").iterdir()):
        if not (folder / "manifest.json").exists():
            continue
        verify(folder)
        metadata = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if all(metadata["source_checksums"].get(name) == value for name, value in current.items()):
            matching.append(folder)
        else:
            obsolete.append(folder.name)
    if not matching:
        raise ValueError("no matching current-version experiment")
    build_report(output, matching, obsolete)
    print(output)


def create_review(path, cases):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        columns = ["case_id", "template_group", "split", "proposed_first_deviation", "status",
                   "reviewer_1", "reviewer_1_verdict", "reviewer_2", "reviewer_2_verdict",
                   "disagreement", "resolution", "evidence_ref", "reviewed_at"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for case in cases:
            writer.writerow({"case_id": case["case_id"], "template_group": case["template_group"],
                             "split": case["split"], "proposed_first_deviation": case["gold"]["first_deviation"],
                             "status": "pending_independent_review"})


def build_report(output, matching, obsolete):
    summaries = {}
    for folder in matching:
        summaries.update(json.loads((folder / "summary.json").read_text(encoding="utf-8")))
    lines = ["# 2 号实验报告：v2 合成试验", "",
             "状态：已执行本地参考实验，标签待两名真实成员独立复核；不是正式产品效果验收。", "",
             "40 条轨迹、4 个独立任务组。开发 20、验证 10、测试 10 条；每条运行 6 种配置，共 240 次。基底提交 ebf4fb52547a2689f7ea72eb6fba54d57a9a04d1，新增工作区源码以 manifest/source_checksums 对应。", "",
             "现有边界检查真实调用；临时文件读写实际执行。单步、序列、来源和继续执行为参考支架，未使用生产 I3/I4。手工契约没有覆盖自动抽取效果。", "",
             "## 留出任务测试结果", "",
             "| 配置 | 计划步骤风险召回 | 正常步骤误报 | 风险轨迹召回 | 正常安全完成 | 脚本继续安全完成 | 禁止副作用轨迹 | 漏检轨迹 |", "|---|---|---|---|---|---|---|---|"]
    for key, row in summaries.items():
        if not key.endswith("/test"):
            continue
        lines.append("| " + " | ".join([key.split("/")[0], fraction(row["planned_step_risk_recall"]),
            fraction(row["planned_step_false_positive_rate"]), fraction(row["planned_trajectory_recall"]),
            fraction(row["normal_safe_success"]), fraction(row["scripted_recovery_safe_success"]),
            str(row["unsafe_effect_trajectories"]), str(row["missed_trajectories"])]) + " |")
    lines.extend(["", "各集合/风险类别/分子分母/延迟/失败详细结果见对应 summary.json。计划步评分包括不可达动作，实际可达判断和副作用单独保存在 raw.jsonl。该表的正常轨迹仅 2 条，风险轨迹 8 条，不能推断真实系统泛化。", "",
                  "## 可支持的结论与失败", "",
                  "现有边界检查会放行部分权限合法的偏离报告，真正完成过的错误写入保留历史标记。参考序列比单步更容易识别重复查询，首个偏移到第三次重复有检测延迟。移除来源后结果相同，当前不能证明来源独立收益。移除继续执行后安全停止的任务不算成功；参考继续读取预录动作，不能证明真实 Planner 恢复能力。", "",
                  "没有真实用户确认交互；确认次数 0 表示支架未调用，不表示生产体验已优化。工具失败原样保存。样本同任务组相关，Wilson 区间仅作描述，不宣称显著提升；正式实验应增加独立任务组。", "",
                  "## 资源预检", ""])
    resource = json.loads((ROOT / "resource-pilot.json").read_text(encoding="utf-8"))
    lines.extend(["| 轨迹数 | worker 峰值 MiB | 含启动墙钟秒 |", "|---|---|---|"])
    for row in resource["measurements"]:
        lines.append(f"| {row['trajectories']} | {row['worker_peak_rss_bytes']/1048576:.2f} | {row['cold_process_wall_seconds']:.3f} |")
    lines.extend(["", "这是每次新启动的 Python worker 进程生命周期峰值，包含导入；200 次为重复轨迹压力回放，不是一条真实长任务。未测服务器、模型、浏览器、数据库或子进程树，不据此声明满足官方 500M。", "",
                  "## 验证与证据", "", "10 项有效性测试通过；运行输出摘要已校验。当前结果目录：", ""])
    lines.extend(f"- `results/{folder.name}`" for folder in matching)
    lines.extend(["", "旧实验保留供审计，不纳入当前结论：", ""])
    lines.extend(f"- `results/{name}`" for name in obsolete)
    lines.extend(["", "## 验收状态", "",
                  "数据生成、严格分组、参考评测执行、原始文件副作用和资料说明已交付。独立标注复核、生产检测/纠偏、真实网关/页面/外发证据、自动抽取评测、全产品资源、批准策略更新、正式 PPT/视频、现场复现和平台提交仍未完成；责任与关闭条件见 WORKFLOW.md。"])
    (output / "experiment-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "evidence-index.json").write_text(json.dumps({"current_runs": [f.name for f in matching],
        "superseded_runs": obsolete, "dataset_sha256": hashlib.sha256((ROOT / "data/pilot.jsonl").read_bytes()).hexdigest(),
        "independent_review": "pending", "platform_submission": "not_submitted"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
