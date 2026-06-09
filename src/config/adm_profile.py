from __future__ import annotations

from src.config.base_profile import AuthorityProfile
from src.models.submittal import DocType


class ADMProfile(AuthorityProfile):

    @property
    def authority(self) -> str:
        return "ADM"

    @property
    def index_items(self) -> dict[int, str]:
        return {
            1:  "BOQ & Drawings",
            2:  "Copies of relevant parts of specifications",
            3:  "Technical comparison table + compliance statement",
            4:  "Manufacturer's technical data and original catalogue",
            5:  "Recent test reports and certificates",
            6:  "Department of Economic Development (DED) registration",
            7:  "Manufacturer/supplier guarantee",
            8:  "Previous approvals (if any)",
            9:  "Applicator's method statement",
            10: "Material Approval Form (MAF) & Material Source Declaration Form (MSDF)",
        }

    @property
    def required_doc_types(self) -> list[DocType]:
        return [
            DocType.COVER_PAGE,
            DocType.BOQ,
            DocType.DRAWING,
            DocType.SPECIFICATION_COPY,
            DocType.COMPARISON_TABLE,
            DocType.TECHNICAL_DATASHEET,
            DocType.TEST_REPORT,
            DocType.DED_REGISTRATION,
            DocType.MANUFACTURER_GUARANTEE,
            DocType.METHOD_STATEMENT,
            DocType.MAF,
            DocType.MSDF,
        ]

    def get_max_doc_age_days(self, doc_type: DocType) -> int | None:
        # DED registration: governed by its own expiry date on the certificate
        # Test reports: max 3 years old per ADM standard
        # Guarantees: period checked against spec requirement, not age
        age_map = {
            DocType.TEST_REPORT: 3 * 365,
        }
        return age_map.get(doc_type)


ADM = ADMProfile()
