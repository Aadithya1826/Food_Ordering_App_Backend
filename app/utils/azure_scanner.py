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
        Returns a list of dictionaries: [{"name": str, "quantity": float, "unit": str}]
        """
        if not self.client:
            raise ValueError("Azure Document Intelligence credentials are not configured in .env")

        poller = self.client.begin_analyze_document(
            "prebuilt-layout",
            body=file_content,
            content_type="application/octet-stream"
        )
        result = poller.result()

        items = []
        
        # Analyze tables found in the document
        if result.tables:
            for table in result.tables:
                # We expect at least 3 columns: Item Name, Quantity, Unit
                # This is a heuristic mapping. We'll try to identify headers.
                
                # Create a grid for the table
                grid = {}
                for cell in table.cells:
                    if cell.row_index not in grid:
                        grid[cell.row_index] = {}
                    grid[cell.row_index][cell.column_index] = cell.content

                # Iterate through rows (skip header row if it looks like one)
                for row_idx in sorted(grid.keys()):
                    row = grid[row_idx]
                    
                    # Basic heuristic: ignore rows with fewer than 2 columns
                    if len(row) < 2:
                        continue
                        
                    # Extract values based on column order (adjust if needed)
                    # Heuristic: 
                    # Col 0: Name
                    # Col 1: Quantity
                    # Col 2: Unit (optional)
                    
                    name = row.get(0, "").strip()
                    qty_str = row.get(1, "").strip()
                    unit = row.get(2, "").strip() if 2 in row else ""

                    # Skip header-like rows or empty names
                    if not name or name.lower() in ["item", "name", "item name", "inventory", "product"]:
                        continue

                    # Try to parse quantity
                    try:
                        # Remove common symbols if any
                        clean_qty = "".join(c for c in qty_str if c.isdigit() or c == '.')
                        quantity = float(clean_qty) if clean_qty else 0.0
                        
                        items.append({
                            "name": name,
                            "quantity": quantity,
                            "unit": unit
                        })
                    except ValueError:
                        # If quantity isn't a number, it might be a header or invalid row
                        continue
        
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
                    merged[name]["quantity"] += item["quantity"]
                else:
                    merged[name] = {
                        "name": name,
                        "quantity": item["quantity"],
                        "unit": item["unit"]
                    }
        
        return list(merged.values())
