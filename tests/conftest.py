import pathlib
import sys
import types

# Stable alias independent of how users name their plugin checkout.
package = types.ModuleType("plugin_under_test")
package.__path__ = [str(pathlib.Path(__file__).resolve().parents[1])]
sys.modules["plugin_under_test"] = package
