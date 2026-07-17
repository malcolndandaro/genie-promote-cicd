import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import reset_demo_ledger  # noqa: E402


class Cursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)


def test_reset_deletes_only_disposable_ledger_tables_in_fk_order():
    cursor = Cursor()
    reset_demo_ledger.reset_with_cursor(cursor)
    assert cursor.statements == [
        f"DELETE FROM {table}" for table in reset_demo_ledger.LEDGER_TABLES
    ]
    statement_text = "\n".join(cursor.statements)
    assert all(table not in statement_text for table in reset_demo_ledger.PRESERVED_CONFIG_TABLES)


def test_reset_contract_preserves_all_operational_configuration_domains():
    assert set(reset_demo_ledger.PRESERVED_CONFIG_TABLES) >= {
        "roles", "rule_overrides", "prompt_template", "ka_endpoints"
    }
