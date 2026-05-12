"""Conversation service 子模块。

这个包只承载 conversation 领域内部拆分，外部代码仍统一从
`app.services.conversation` 导入 facade，避免调用方绑定到内部结构。
"""
