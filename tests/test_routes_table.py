"""路由表护栏:校验 serve.py 的 _ROUTES_EXACT / _ROUTES_PREFIX 声明完整、
handler 名真实存在、参数合法、关键端点未漏注册。

改路由分发时此测试应保持绿色;handler 改名后这里会先红,提示同步更新路由表。
不覆盖 _dispatch 运行时匹配(那由冒烟脚本覆盖),只保证路由表声明本身正确。"""
import unittest
from serve import DashboardHandler

_METHODS = {"GET", "POST", "DELETE", "PATCH"}
_PARAMS = {None, "name", "path"}


class TestRoutesTable(unittest.TestCase):

    def test_exact_handlers_exist(self):
        """每条精确路由的 handler 名必须在 DashboardHandler 上可调用。"""
        for (method, path), (handler, param) in DashboardHandler._ROUTES_EXACT.items():
            self.assertTrue(
                callable(getattr(DashboardHandler, handler, None)),
                f"精确路由 ({method}, {path}) → 不存在的 handler '{handler}'",
            )

    def test_prefix_handlers_exist(self):
        """每条前缀路由的 handler 名必须可调用。"""
        for method, prefix, suffix, handler, param in DashboardHandler._ROUTES_PREFIX:
            self.assertTrue(
                callable(getattr(DashboardHandler, handler, None)),
                f"前缀路由 {method} {prefix}..{suffix} → 不存在的 handler '{handler}'",
            )

    def test_methods_and_params_valid(self):
        """HTTP method 合法;param 只能是 None/name/path。"""
        for (method, path), (handler, param) in DashboardHandler._ROUTES_EXACT.items():
            self.assertIn(method, _METHODS, f"{path} 非法 method: {method}")
            self.assertIn(param, _PARAMS, f"({method},{path}) 非法 param: {param}")
        for method, prefix, suffix, handler, param in DashboardHandler._ROUTES_PREFIX:
            self.assertIn(method, _METHODS, f"{prefix} 非法 method: {method}")
            self.assertIn(param, _PARAMS, f"{method} {prefix} 非法 param: {param}")

    def test_prefix_rules_well_formed(self):
        for method, prefix, suffix, handler, param in DashboardHandler._ROUTES_PREFIX:
            self.assertTrue(prefix.startswith("/"), f"prefix 必须以 / 开头: {prefix}")
            self.assertIsInstance(suffix, str)

    def test_critical_endpoints_registered(self):
        """关键端点必须在表里——防止重构时漏注册。"""
        keys = set(DashboardHandler._ROUTES_EXACT.keys())
        for key in [("GET", "/api/fast-scan"), ("GET", "/api/targets"),
                    ("GET", "/api/openapi"), ("POST", "/api/scan-run"),
                    ("DELETE", "/api/trash"), ("GET", "/api/trash/stats")]:
            self.assertIn(key, keys, f"关键端点未在精确表注册: {key}")
        prefixes = {(m, p, s) for m, p, s, *_ in DashboardHandler._ROUTES_PREFIX}
        for rule in [("GET", "/static/", ""), ("GET", "/api/skill/", "/content"),
                     ("GET", "/api/skill/", "/upstream"), ("DELETE", "/api/skill/", ""),
                     ("PATCH", "/api/skill/", "/fix")]:
            self.assertIn(rule, prefixes, f"关键前缀路由未注册: {rule}")


if __name__ == "__main__":
    unittest.main()
