"""
litdiscovery.agent.orchestrator —— planner 路由 + executor 确定性执行编排层。

planner 不掌握具体工具，只持有 AGENT_DIRECTORY，把用户指令编排为
plan.v3.json（agent 链 + 全参数软设置）；executor 按 plan 逐 agent 确定性执行。

    planner.py         LLM 路由：生成 plan.v3.json → 确认 → 落盘
    agent_directory.py 子 Agent 目录（能力 + 参数 schema + 默认步骤模板）
    params.py          参数参考库（软设置默认值单一事实源）
    plan.py            plan.v3.json 契约 + plan→runbook 翻译
    pipeline.py        executor：run_pipeline（runbook）/ run_plan（plan v3）
    report.py          聚合产物 → report_writer 生成调研报告

统一 CLI（cli.main）的 run/pipeline 子命令即调研流水线入口。
"""
