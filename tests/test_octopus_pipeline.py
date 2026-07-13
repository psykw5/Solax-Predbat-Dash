from __future__ import annotations

import unittest
from datetime import UTC, datetime

from ingestion.octopus_pipeline import extract_electricity_agreements, product_code_from_tariff


class OctopusPipelineTests(unittest.TestCase):
    def test_product_code_from_single_register_tariff(self) -> None:
        self.assertEqual(
            product_code_from_tariff("E-1R-FLUX-IMPORT-23-02-14-A"),
            "FLUX-IMPORT-23-02-14",
        )

    def test_extract_import_and_export_agreements(self) -> None:
        payload = {
            "properties": [
                {
                    "electricity_meter_points": [
                        {
                            "mpan": "111",
                            "is_export": False,
                            "agreements": [
                                {
                                    "tariff_code": "E-1R-IMPORT-A",
                                    "valid_from": "2023-01-01T00:00:00Z",
                                    "valid_to": "2023-06-01T00:00:00Z",
                                }
                            ],
                        },
                        {
                            "mpan": "222",
                            "is_export": True,
                            "agreements": [
                                {
                                    "tariff_code": "E-1R-EXPORT-A",
                                    "valid_from": "2023-01-01T00:00:00Z",
                                    "valid_to": None,
                                }
                            ],
                        },
                    ]
                }
            ]
        }

        agreements = extract_electricity_agreements(
            payload, "https://api.octopus.energy/v1/accounts/redacted/", datetime.now(UTC)
        )

        self.assertEqual({agreement.direction for agreement in agreements}, {"import", "export"})
        self.assertTrue(all(agreement.meter_point_id.startswith("mp_") for agreement in agreements))
        self.assertEqual({agreement.product_code for agreement in agreements}, {"IMPORT", "EXPORT"})


if __name__ == "__main__":
    unittest.main()
