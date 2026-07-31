import os
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

load_dotenv()

class AzureScanner:
    def __init__(self):
        self.endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        self.key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        
        if not self.endpoint or not self.key:
            # We'll allow initialization but methods will fail if keys are missing
            self.client = None
        else:
            self.client = DocumentIntelligenceClient(self.endpoint, AzureKeyCredential(self.key))

    def scan_inventory_sheet(self, file_content):
        """
        Scans an image of an inventory sheet and returns extracted items.
        Returns a list of dictionaries with inventory fields.
        """
        if not self.client:
            raise ValueError("Azure Document Intelligence credentials are not configured in .env")

        poller = self.client.begin_analyze_document(
            "prebuilt-layout",
            analyze_request=file_content,
            content_type="application/octet-stream"
        )
        result = poller.result()

        items = []
        import re

        def parse_number(val_str):
            if not val_str:
                return 0.0
            val_str = str(val_str).strip()
            if val_str == '-' or not val_str:
                return 0.0
            
            # Match formats like "1 1/2", "3/4"
            match = re.search(r'(?:(\d+)\s+)?(\d+)/(\d+)', val_str)
            if match:
                whole = float(match.group(1)) if match.group(1) else 0.0
                num = float(match.group(2))
                den = float(match.group(3))
                if den != 0:
                    return whole + (num / den)
                    
            clean_val = "".join(c for c in val_str if c.isdigit() or c == '.')
            try:
                return float(clean_val) if clean_val else 0.0
            except ValueError:
                return 0.0

        def extract_unit(val_str):
            if not val_str:
                return "units"
            clean = "".join(c for c in str(val_str) if c.isalpha() or c.isspace()).strip()
            return clean if clean else "units"
        
        # Analyze tables found in the document
        if result.tables:
            for table in result.tables:
                # Create a grid for the table
                grid = {}
                for cell in table.cells:
                    if cell.row_index not in grid:
                        grid[cell.row_index] = {}
                    grid[cell.row_index][cell.column_index] = cell.content

                # Iterate through rows
                for row_idx in sorted(grid.keys()):
                    row = grid[row_idx]
                    
                    # We expect inventory rows to have at least 4 columns (Name, Open, Purchase, Total...)
                    # This filters out the 'Sales' table on the right side which only has 2 columns.
                    if len(row) < 4:
                        continue
                        
                    name = str(row.get(0, "")).strip()
                    
                    # Skip header-like rows or irrelevant receipt data
                    skip_words = ["item", "name", "inventory", "product", "stock statement", "sales", "unit", "date"]
                    if not name or any(word in name.lower() for word in skip_words) or name.startswith("Print Date") or name.startswith("Bills From"):
                        continue

                    open_stock = parse_number(row.get(1, ""))
                    purchase = parse_number(row.get(2, ""))
                    total = parse_number(row.get(3, ""))
                    issue = parse_number(row.get(4, ""))
                    balance = parse_number(row.get(5, ""))
                    
                    # The physical sheet does not have a unit column in the data rows.
                    # Column 6 is actually the start of the Sales table on the right side!
                    # So we hardcode unit to "units".
                    unit = "units"

                    items.append({
                        "name": name,
                        "open_stock": open_stock,
                        "purchase": purchase,
                        "total": total,
                        "issue": issue,
                        "balance": balance,
                        "unit": unit
                    })
        
        return items

    def merge_scanned_results(self, results_list):
        """
        Merges items from multiple scans (e.g. front and back).
        Deduplicates by name, summing quantities if they match.
        """
        merged = {}
        for items in results_list:
            for item in items:
                name = item["name"].title() # Normalize casing
                if name in merged:
                    merged[name]["open_stock"] += item["open_stock"]
                    merged[name]["purchase"] += item["purchase"]
                    merged[name]["total"] += item["total"]
                    merged[name]["issue"] += item["issue"]
                    merged[name]["balance"] += item["balance"]
                else:
                    merged[name] = item
                    merged[name]["name"] = name
        
        return list(merged.values())
