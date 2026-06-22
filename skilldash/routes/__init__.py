"""skilldash.routes — HTTP 路由 handler 按 domain 拆分的 mixin 集合。

每个 domain 一个 mixin 类,DashboardHandler 多继承它们。handler 逻辑从 serve.py
原样搬出,self 引用不变;搬动是纯机械重构,行为零变化。

- system.py : history / category-order / openapi 等系统级路由
"""
