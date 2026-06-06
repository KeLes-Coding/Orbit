"""Data Workspace Agent definition 预留位。

第一版闭环先把 graph 策略留在 runtime.py，避免过早抽象。
保留这个模块是为了让插件形态先对齐 web_agent 的
adapter/runtime/definition/projector 结构；后续接入 LLM codegen/repair
时，不需要改 Orbit 宿主 runtime。
"""
