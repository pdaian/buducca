import importlib
import sys


sys.modules[__name__] = importlib.import_module("scripts.run_client")
