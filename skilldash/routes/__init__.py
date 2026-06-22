"""skilldash.routes — HTTP 路由 handler 按 domain 拆分的 mixin 集合。

每个 domain 一个 mixin 类,DashboardHandler 多继承它们(SkillRoutes, SourceRoutes,
CleanupRoutes, ScanRoutes, SystemRoutes)。handler 逻辑从 serve.py 原样搬出,
self 引用不变;搬动是纯机械重构,行为零变化。

跨域共享:
- serve.py 基类提供基础设施(_dispatch/_json_response/_read_json/_check_csrf/
  _serve_file 等)+ 运行态缓存 self._targets_cache_hit/_store/_invalidate_runtime_caches。
- skilldash.source_ops 提供 GitHub 业务(install_skill/check_upstream_status/...),
  各 domain 顶层 import 调用,顶层 import 不依赖 serve,无循环。

- system.py  : history / category-order / openapi
- source.py  : 目标库列表/切换、来源 skill 列表/搜索、自定义来源、GitHub 安装、本机插件
- skill.py   : 单 skill 内容/预览、上游检查/更新、修复、删除、复制、rehash
- cleanup.py : 目录治理计划/执行、重复决策、垃圾站、批量删除(含跨域 _trash_dir)
- scan.py    : fast-scan/二哥扫描/全域统计/理解/诊断(含 _diag_* 运行态状态)
"""
