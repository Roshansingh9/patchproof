from pathlib import Path

from conftest import SAMPLE_REPO, write_repo

from patchproof.graph import CALLS, IMPORTS, INHERITS, build_graph, is_test_file
from patchproof.indexer import index_repository


def graph_for(root: Path):
    return build_graph(index_repository(root.resolve()))


def edges(cg, kind):
    return {(u, v) for u, v, k in cg.graph.edges(keys=True) if k == kind}


def test_sample_repo_edges():
    cg = graph_for(SAMPLE_REPO)
    calls = edges(cg, CALLS)
    assert ("shop.pricing.compute_total", "shop.coupons.parse_coupon") in calls
    assert ("shop.pricing.compute_total", "shop.pricing.apply_discount") in calls
    assert ("shop.cart.Cart.checkout_total", "shop.pricing.compute_total") in calls
    # super().line_total() resolves to the base-class method
    assert ("shop.models.DiscountedItem.line_total", "shop.models.Item.line_total") in calls
    # local variable typed by constructor: cart = Cart(); cart.add(...)
    assert ("tests.test_cart.test_total_without_coupon", "shop.cart.Cart.add") in calls
    assert ("tests.test_cart.test_total_without_coupon", "shop.cart.Cart.__init__") in calls
    assert ("shop.models.DiscountedItem", "shop.models.Item") in edges(cg, INHERITS)
    assert ("shop.pricing", "shop.coupons.parse_coupon") in edges(cg, IMPORTS)
    # item.line_total() over an untyped loop variable is left unresolved
    assert cg.graph.nodes["shop.pricing.subtotal"].get("unresolved_calls") == 1
    assert cg.stats["test_functions"] == 4


def test_relative_imports_and_reexports(tmp_path):
    write_repo(tmp_path, {
        "pkg/__init__.py": "from .core import run\n",
        "pkg/core.py": "from .util.helpers import helper\n\ndef run():\n    return helper()\n",
        "pkg/util/__init__.py": "",
        "pkg/util/helpers.py": "from ..core import run\n\ndef helper():\n    return 1\n",
        "app.py": "import pkg\nimport pkg.util.helpers as h\n\ndef main():\n    pkg.run()\n    h.helper()\n",
    })
    cg = graph_for(tmp_path)
    calls = edges(cg, CALLS)
    assert ("pkg.core.run", "pkg.util.helpers.helper") in calls
    assert ("app.main", "pkg.core.run") in calls  # via re-export in pkg/__init__.py
    assert ("app.main", "pkg.util.helpers.helper") in calls
    assert ("pkg.util.helpers", "pkg.core.run") in edges(cg, IMPORTS)


def test_ambiguous_binding_left_unresolved(tmp_path):
    write_repo(tmp_path, {
        "a.py": "def f():\n    return 1\n",
        "b.py": "def f():\n    return 2\n",
        "c.py": "try:\n    from a import f\nexcept ImportError:\n    from b import f\n\ndef g():\n    return f()\n",
    })
    cg = graph_for(tmp_path)
    assert not [e for e in edges(cg, CALLS) if e[0] == "c.g"]
    assert cg.graph.nodes["c.g"]["unresolved_calls"] == 1
    assert cg.stats["ambiguous_bindings"] == 1


def test_method_resolution_through_inheritance(tmp_path):
    write_repo(tmp_path, {
        "m.py": (
            "class Base:\n"
            "    def save(self):\n        return 1\n"
            "class Child(Base):\n"
            "    def run(self):\n        return self.save()\n"
            "def use():\n"
            "    c = Child()\n    c.save()\n    Child.save(c)\n"
            "def unknown(obj):\n    obj.save()\n"
        ),
    })
    cg = graph_for(tmp_path)
    calls = edges(cg, CALLS)
    assert ("m.Child.run", "m.Base.save") in calls
    assert ("m.use", "m.Base.save") in calls
    assert ("m.use", "m.Child") in calls
    assert cg.graph.nodes["m.unknown"]["unresolved_calls"] == 1


def test_external_calls_not_edges(tmp_path):
    write_repo(tmp_path, {"x.py": "import json\n\ndef f(s):\n    print(s)\n    return json.loads(s)\n"})
    cg = graph_for(tmp_path)
    assert not edges(cg, CALLS)
    assert cg.stats["external_or_builtin_calls"] == 2


def test_test_discovery(tmp_path):
    write_repo(tmp_path, {
        "tests/test_a.py": (
            "import unittest\n"
            "def test_one():\n    pass\n"
            "def helper():\n    pass\n"
            "class TestThing:\n    def test_two(self):\n        pass\n"
            "class Cases(unittest.TestCase):\n    def test_three(self):\n        pass\n"
        ),
        "lib.py": "def test_not_a_test():\n    pass\n",
    })
    cg = graph_for(tmp_path)
    names = sorted(s.qname for s in cg.tests())
    assert names == ["tests.test_a.Cases.test_three", "tests.test_a.TestThing.test_two", "tests.test_a.test_one"]
    assert is_test_file("pkg/foo_test.py") and not is_test_file("pkg/tests/__init__.py")
