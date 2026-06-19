"""命名空间包：允许与其他 tests 目录共存（provider/workflow 的 tests 也是同名包）。"""
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
