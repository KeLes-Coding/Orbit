"""Data Workspace analysis.py 的执行前预检。"""

from __future__ import annotations

import ast


class DataCodeValidator:
    """用 AST 校验模型代码是否遵守 Orbit runtime contract。"""

    _BANNED_IMPORT_ROOTS = {
        "numpy",
        "openpyxl",
        "pandas",
        "polars",
        "requests",
        "socket",
        "subprocess",
    }
    _HELPER_ARITY = {
        "save_json_artifact": 2,
        "save_text_artifact": 2,
    }
    _ALLOWED_ARTIFACT_TYPES = {"table", "chart", "code", "text", "report", "json"}

    def validate(self, code: str) -> str | None:
        """返回预检错误；返回 None 表示可以进入 sandbox 执行。"""

        try:
            tree = ast.parse(code)
            compile(code, "analysis.py", "exec")
        except SyntaxError as exc:
            return f"analysis.py 预检失败：Python 语法错误：{exc.msg} (line {exc.lineno})。"

        helper_calls: set[str] = set()
        manifest_types = self._collect_literal_manifest_types(tree)

        for node in ast.walk(tree):
            import_error = self._validate_import(node)
            if import_error:
                return import_error

            call_error = self._validate_call(node)
            if call_error:
                return call_error

            if isinstance(node, ast.Call):
                name = self._call_name(node)
                if name:
                    helper_calls.add(name)

        if "write_artifact_manifest" not in helper_calls:
            return (
                "analysis.py 预检失败：脚本必须调用 write_artifact_manifest([...]) "
                "写出 output/artifact_manifest.json。"
            )

        # 只有 manifest 是字面量时才强校验产物类型；动态构造交给 sandbox 后的 manifest collector 校验。
        if manifest_types is not None:
            if "report" not in manifest_types or "code" not in manifest_types:
                return "analysis.py 预检失败：artifact_manifest 必须至少包含 report 和 code 两类产物。"

        return None

    def _validate_import(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in self._BANNED_IMPORT_ROOTS:
                    return self._banned_import_error(root)
        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in self._BANNED_IMPORT_ROOTS:
                return self._banned_import_error(root)
        return None

    def _validate_call(self, node: ast.AST) -> str | None:
        if not isinstance(node, ast.Call):
            return None
        name = self._call_name(node)
        if name in {"os.system", "subprocess.run", "subprocess.Popen", "subprocess.call"}:
            return f"analysis.py 预检失败：禁止调用 {name}。"
        if name not in self._HELPER_ARITY:
            return None

        expected_arity = self._HELPER_ARITY[name]
        if len(node.args) != expected_arity or node.keywords:
            return (
                f"analysis.py 预检失败：{name} 只能按位置参数调用 "
                f"{name}(path, value)，不能使用 name/content/group 等关键字或额外参数。"
            )
        path_error = self._validate_output_path_arg(name, node.args[0])
        if path_error:
            return path_error
        return None

    def _validate_output_path_arg(self, helper_name: str, node: ast.AST) -> str | None:
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return None
        path = node.value.replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/"):
            return f"analysis.py 预检失败：{helper_name} 的 path 必须是 output 内相对路径。"
        return None

    def _collect_literal_manifest_types(self, tree: ast.AST) -> set[str] | None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or self._call_name(node) != "write_artifact_manifest":
                continue
            if not node.args or not isinstance(node.args[0], ast.List):
                return None
            types: set[str] = set()
            for item in node.args[0].elts:
                if not isinstance(item, ast.Dict):
                    return None
                artifact_type = self._dict_string_value(item, "type")
                if artifact_type is None:
                    return None
                if artifact_type not in self._ALLOWED_ARTIFACT_TYPES:
                    return {artifact_type}
                types.add(artifact_type)
            return types
        return set()

    @staticmethod
    def _dict_string_value(node: ast.Dict, key: str) -> str | None:
        for key_node, value_node in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key_node, ast.Constant)
                and key_node.value == key
                and isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
            ):
                return value_node.value
        return None

    @staticmethod
    def _call_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        return None

    @staticmethod
    def _banned_import_error(module: str) -> str:
        return (
            f"analysis.py 预检失败：sandbox 不允许导入 {module}；"
            "请只使用 Python 标准库和 orbit_data helper。"
        )
