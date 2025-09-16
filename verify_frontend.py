import re
from playwright.sync_api import Page, expect
import time
import pytest

def test_scenario_view(page: Page):
    # It's good practice to dismiss dialogs unless they are the subject of the test.
    # In this case, we expect no dialogs. If one appears, the test should fail.
    def handle_dialog(dialog):
        message = dialog.message
        dialog.dismiss() # Dismiss the dialog to not hang the test
        pytest.fail(f"Test failed because an unexpected alert appeared: {message}")

    page.on("dialog", handle_dialog)

    # Navigate to the dashboard at the correct port
    page.goto("http://localhost:5000/dashboard")

    # Click the "Scenarios" tab
    page.locator('.view-tab[data-view="scenario"]').click()

    # Locate the scenario view
    scenario_view = page.locator("#scenario-view")
    expect(scenario_view).to_be_visible(timeout=10000)

    # Locate the product dropdown
    scenario_product_select = page.locator("#scenarioProductSelect")

    # Wait for the "Product A" option to be added to the DOM
    expect(scenario_product_select.locator('option[value="Product A"]')).to_have_count(1, timeout=10000)

    # Select "Product A"
    scenario_product_select.select_option("Product A")

    # *** FIX: Add an assertion to wait for the selection to be processed ***
    expect(scenario_product_select).to_have_value("Product A", timeout=5000)

    # Click the "Run Prioritization Scenario" button
    page.locator("#runScenarioBtn").click()

    # Wait for the results container to appear.
    result_div = page.locator("#scenarioResult")
    expect(result_div).to_be_visible(timeout=20000)

    # Check for the title of the results
    result_title = page.locator("#scenarioResultTitle")
    expect(result_title).to_contain_text("Results for Prioritizing: Product A")

    # Check that the comparison table has rows using a standard assert
    comparison_rows = page.locator("#comparison-body .comparison-row")
    assert comparison_rows.count() > 0, "The comparison table should have at least one row."

    # Take a screenshot for verification
    page.screenshot(path="runs/screenshot.png")
