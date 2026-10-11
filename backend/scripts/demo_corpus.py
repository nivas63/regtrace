"""SYNTHETIC demonstration corpus for RegTrace.

Every document here is fictional. The regulations are attributed to fictional
authorities ("Synthetic Regulatory Authority", "Synthetic Data Protection
Authority"). They imitate the drafting structure of Indian banking regulation
(numbered paragraphs, chapters, effective-date clauses) but are NOT actual
regulatory instruments and must never be presented as such.

The same data drives (a) the generated PDFs/XLSX the platform ingests and
(b) the golden labels used by the evaluation harness, so the expected answers are
known exactly. Golden labels were written by the same author as the documents -
a known limitation of this evaluation set (see docs).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DISCLAIMER = (
    "SYNTHETIC DEMONSTRATION DOCUMENT. Modelled on the structure of banking "
    "regulation for evaluation purposes only. Not an actual regulatory instrument."
)


@dataclass(frozen=True)
class GoldObligation:
    quote: str  # verbatim substring of the paragraph text
    obligation_type: str
    modality: str = "MUST"
    period: str | None = None  # ISO-8601 for periodic obligations


@dataclass(frozen=True)
class Para:
    path: str
    text: str
    title: str | None = None
    obligations: tuple[GoldObligation, ...] = ()


@dataclass(frozen=True)
class Chapter:
    number: str
    title: str
    paras: tuple[Para, ...]


@dataclass(frozen=True)
class RegulationDoc:
    doc_id: str
    title: str
    regulator: str
    ref_no: str
    dated: str
    effective: str
    preamble: str
    chapters: tuple[Chapter, ...]
    filename: str
    extra_front: str = ""


O = GoldObligation
P = Para

# ---------------------------------------------------------------------------
# Regulation 1: KYC Directions, version 1 (2024)
# ---------------------------------------------------------------------------

_KYC_COMMON_CH1 = Chapter(
    "I",
    "Preliminary",
    (
        P("1", "", "Short Title and Commencement"),
        P("1.1", "These Directions shall be called the Master Direction on Know Your Customer."),
        P("2", "", "Applicability"),
        P(
            "2.1",
            "The provisions of these Directions shall apply to every scheduled commercial bank "
            "and small finance bank operating in India, hereinafter referred to as the Regulated "
            "Entity (RE).",
        ),
    ),
)


def _kyc_chapters(v2: bool) -> tuple[Chapter, ...]:
    review_62 = "three months" if v2 else "six months"
    period_62 = "P3M" if v2 else "P6M"
    high_71 = "once every year" if v2 else "once every two years"
    period_71 = "P1Y" if v2 else "P2Y"
    alert_days = "15 days" if v2 else "30 days"
    para_32 = (
        "The Board shall review the KYC policy not less than once a year."
        if v2
        else "The KYC policy shall be reviewed by the Board at least once a year."
    )
    quote_32 = (
        "review the KYC policy not less than once a year"
        if v2
        else ("The KYC policy shall be reviewed by the Board at least once a year")
    )
    vcip = [
        P(
            "8.1",
            "The RE may undertake V-CIP for customer onboarding, subject to the conditions "
            "specified in this paragraph.",
            obligations=(O("The RE may undertake V-CIP for customer onboarding", "CUSTOMER_DUE_DILIGENCE", "MAY"),),
        ),
        P(
            "8.2",
            "The V-CIP interaction shall be recorded and the recording shall be stored in a safe and secure manner.",
            obligations=(
                O(
                    "The V-CIP interaction shall be recorded and the recording shall be stored in a safe and secure manner",
                    "RECORD_RETENTION",
                ),
            ),
        ),
    ]
    if v2:
        vcip.append(
            P(
                "8.3",
                "The audit trail of each V-CIP session, including the geo-location and "
                "timestamp of the customer, shall be retained for a period of five years.",
                obligations=(
                    O(
                        "The audit trail of each V-CIP session, including the geo-location and timestamp of the customer, shall be retained for a period of five years",
                        "RECORD_RETENTION",
                    ),
                ),
            )
        )
    return (
        _KYC_COMMON_CH1,
        Chapter(
            "II",
            "KYC Policy and Governance",
            (
                P("3", "", "KYC Policy"),
                P(
                    "3.1",
                    "Every RE shall have a Know Your Customer (KYC) policy duly approved by its Board of Directors.",
                    obligations=(
                        O(
                            "Every RE shall have a Know Your Customer (KYC) policy duly approved by its Board of Directors",
                            "GOVERNANCE",
                        ),
                    ),
                ),
                P("3.2", para_32, obligations=(O(quote_32, "GOVERNANCE", period="P1Y"),)),
                P("4", "", "Designated Officials"),
                P(
                    "4.1",
                    "The RE shall nominate a Principal Officer who shall be responsible for "
                    "ensuring compliance, monitoring transactions, and sharing and reporting "
                    "information to the Financial Intelligence Unit (FIU).",
                    obligations=(O("The RE shall nominate a Principal Officer", "GOVERNANCE"),),
                ),
                P(
                    "4.2",
                    "The name and contact details of the Principal Officer shall be communicated "
                    "to the FIU within 30 days of the appointment.",
                    obligations=(
                        O(
                            "The name and contact details of the Principal Officer shall be communicated to the FIU within 30 days of the appointment",
                            "REPORTING",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "III",
            "Customer Due Diligence",
            (
                P("5", "", "Customer Identification"),
                P(
                    "5.1",
                    "The RE shall undertake customer due diligence (CDD) at the time of "
                    "commencement of an account-based relationship, by identifying the customer "
                    "and verifying the identity using officially valid documents.",
                    obligations=(
                        O(
                            "The RE shall undertake customer due diligence (CDD) at the time of commencement of an account-based relationship",
                            "CUSTOMER_DUE_DILIGENCE",
                        ),
                    ),
                ),
                P(
                    "5.2",
                    "The RE shall not open an account where it is unable to apply appropriate CDD measures.",
                    obligations=(
                        O(
                            "The RE shall not open an account where it is unable to apply appropriate CDD measures",
                            "CUSTOMER_DUE_DILIGENCE",
                            "MUST_NOT",
                        ),
                    ),
                ),
                P(
                    "5.3",
                    "Where the customer is a legal entity, the RE shall identify the beneficial "
                    "owner holding more than 10 per cent of the ownership interest.",
                    obligations=(
                        O(
                            "the RE shall identify the beneficial owner holding more than 10 per cent of the ownership interest",
                            "CUSTOMER_DUE_DILIGENCE",
                        ),
                    ),
                ),
                P("6", "", "Risk Categorisation"),
                P(
                    "6.1",
                    "The RE shall categorise customers as low, medium or high risk based on the "
                    "assessment of money laundering and terrorist financing risk.",
                    obligations=(
                        O("The RE shall categorise customers as low, medium or high risk", "RISK_ASSESSMENT"),
                    ),
                ),
                P(
                    "6.2",
                    f"The risk categorisation of customers shall be reviewed at least once in {review_62}.",
                    obligations=(
                        O(
                            f"The risk categorisation of customers shall be reviewed at least once in {review_62}",
                            "RISK_ASSESSMENT",
                            period=period_62,
                        ),
                    ),
                ),
                P("7", "", "Periodic Updation"),
                P(
                    "7.1",
                    f"The RE shall carry out periodic updation of KYC at least {high_71} for high risk customers.",
                    obligations=(
                        O(
                            f"The RE shall carry out periodic updation of KYC at least {high_71} for high risk customers",
                            "CUSTOMER_DUE_DILIGENCE",
                            period=period_71,
                        ),
                    ),
                ),
                P(
                    "7.2",
                    "Periodic updation shall be carried out at least once every eight years for medium risk customers.",
                    obligations=(
                        O(
                            "Periodic updation shall be carried out at least once every eight years for medium risk customers",
                            "CUSTOMER_DUE_DILIGENCE",
                            period="P8Y",
                        ),
                    ),
                ),
                P(
                    "7.3",
                    "Periodic updation shall be carried out at least once every ten years for low risk customers.",
                    obligations=(
                        O(
                            "Periodic updation shall be carried out at least once every ten years for low risk customers",
                            "CUSTOMER_DUE_DILIGENCE",
                            period="P10Y",
                        ),
                    ),
                ),
                P("8", "", "Video-based Customer Identification Process"),
                *vcip,
            ),
        ),
        Chapter(
            "IV",
            "Record Management and Reporting",
            (
                P("9", "", "Record Retention"),
                P(
                    "9.1",
                    "The RE shall maintain all necessary records of transactions for at least "
                    "five years from the date of transaction.",
                    obligations=(
                        O(
                            "The RE shall maintain all necessary records of transactions for at least five years from the date of transaction",
                            "RECORD_RETENTION",
                        ),
                    ),
                ),
                P(
                    "9.2",
                    "The RE shall preserve the records pertaining to the identification of "
                    "customers for at least five years after the business relationship has ended.",
                    obligations=(
                        O(
                            "The RE shall preserve the records pertaining to the identification of customers for at least five years after the business relationship has ended",
                            "RECORD_RETENTION",
                        ),
                    ),
                ),
                P("10", "", "Reporting to the FIU"),
                P(
                    "10.1",
                    "The RE shall furnish Suspicious Transaction Reports (STR) to the FIU within "
                    "seven working days of arriving at a conclusion that a transaction is "
                    "suspicious.",
                    obligations=(
                        O(
                            "The RE shall furnish Suspicious Transaction Reports (STR) to the FIU within seven working days",
                            "REPORTING",
                        ),
                    ),
                ),
                P(
                    "10.2",
                    "The RE shall furnish Cash Transaction Reports (CTR) for every month to the "
                    "FIU by the 15th day of the succeeding month.",
                    obligations=(
                        O(
                            "The RE shall furnish Cash Transaction Reports (CTR) for every month to the FIU by the 15th day of the succeeding month",
                            "REPORTING",
                            period="P1M",
                        ),
                    ),
                ),
                P(
                    "10.3",
                    "The RE and its employees shall not disclose to the customer that an STR has been filed.",
                    obligations=(
                        O(
                            "The RE and its employees shall not disclose to the customer that an STR has been filed",
                            "REPORTING",
                            "MUST_NOT",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "V",
            "Monitoring, Training and Audit",
            (
                P("11", "", "Transaction Monitoring"),
                P(
                    "11.1",
                    "The RE shall put in place an automated system to monitor transactions and "
                    "generate alerts for unusual patterns of activity.",
                    obligations=(
                        O(
                            "The RE shall put in place an automated system to monitor transactions and generate alerts",
                            "MONITORING",
                        ),
                    ),
                ),
                P(
                    "11.2",
                    f"Every alert generated by the transaction monitoring system shall be "
                    f"reviewed and closed within {alert_days} of its generation.",
                    obligations=(
                        O(
                            f"Every alert generated by the transaction monitoring system shall be reviewed and closed within {alert_days} of its generation",
                            "MONITORING",
                        ),
                    ),
                ),
                P("12", "", "Training"),
                P(
                    "12.1",
                    "The RE shall conduct KYC and anti-money laundering training for all "
                    "frontline staff at least once a year.",
                    obligations=(
                        O(
                            "The RE shall conduct KYC and anti-money laundering training for all frontline staff at least once a year",
                            "TRAINING",
                            period="P1Y",
                        ),
                    ),
                ),
                P("13", "", "Internal Audit"),
                P(
                    "13.1",
                    "Compliance with the KYC policy shall be verified by the internal audit "
                    "function at least once every quarter.",
                    obligations=(
                        O(
                            "Compliance with the KYC policy shall be verified by the internal audit function at least once every quarter",
                            "TESTING_AUDIT",
                            period="P3M",
                        ),
                    ),
                ),
            ),
        ),
    )


KYC_V1 = RegulationDoc(
    doc_id="SYN-KYC-v1",
    title="Master Direction on Know Your Customer (KYC)",
    regulator="Synthetic Regulatory Authority",
    ref_no="SYN/KYC/2024/01",
    dated="January 15, 2024",
    effective="April 1, 2024",
    preamble=(
        "In exercise of the powers conferred on it, the Synthetic Regulatory Authority hereby "
        "issues the following Directions. These Directions shall come into force on April 1, 2024."
    ),
    chapters=_kyc_chapters(v2=False),
    filename="SYN_KYC_Master_Direction_2024_v1.pdf",
)

KYC_V2 = RegulationDoc(
    doc_id="SYN-KYC-v2",
    title="Master Direction on Know Your Customer (KYC)",
    regulator="Synthetic Regulatory Authority",
    ref_no="SYN/KYC/2025/07",
    dated="June 10, 2025",
    effective="October 1, 2025",
    preamble=(
        "In supersession of SYN/KYC/2024/01, the Synthetic Regulatory Authority hereby issues "
        "the following updated Directions. These Directions shall come into force on "
        "October 1, 2025."
    ),
    chapters=_kyc_chapters(v2=True),
    filename="SYN_KYC_Master_Direction_2025_v2.pdf",
)

# ---------------------------------------------------------------------------
# Regulation 2: IT Governance Directions (2024)
# ---------------------------------------------------------------------------

ITG = RegulationDoc(
    doc_id="SYN-ITG-v1",
    title="Master Direction on Information Technology Governance, Risk and Controls",
    regulator="Synthetic Regulatory Authority",
    ref_no="SYN/ITG/2024/03",
    dated="March 20, 2024",
    effective="July 1, 2024",
    preamble=(
        "The Synthetic Regulatory Authority issues these Directions to strengthen IT "
        "governance and cyber resilience. These Directions shall come into force on July 1, 2024."
    ),
    chapters=(
        Chapter(
            "I",
            "Preliminary",
            (
                P("1", "", "Short Title"),
                P("1.1", "These Directions shall be called the Master Direction on IT Governance."),
            ),
        ),
        Chapter(
            "II",
            "IT Governance",
            (
                P("2", "", "Board Oversight"),
                P(
                    "2.1",
                    "The RE shall constitute an IT Strategy Committee of the Board which shall "
                    "meet at least once every quarter.",
                    obligations=(
                        O(
                            "The RE shall constitute an IT Strategy Committee of the Board which shall meet at least once every quarter",
                            "GOVERNANCE",
                            period="P3M",
                        ),
                    ),
                ),
                P(
                    "2.2",
                    "The RE shall appoint a Chief Information Security Officer (CISO) who shall "
                    "report directly to the Executive Director or equivalent.",
                    obligations=(
                        O(
                            "The RE shall appoint a Chief Information Security Officer (CISO) who shall report directly to the Executive Director or equivalent",
                            "GOVERNANCE",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "III",
            "Access Control",
            (
                P("3", "", "Privileged Access"),
                P(
                    "3.1",
                    "The RE shall implement multi-factor authentication for all privileged users "
                    "accessing critical systems.",
                    obligations=(
                        O(
                            "The RE shall implement multi-factor authentication for all privileged users accessing critical systems",
                            "TECHNICAL_CONTROL",
                        ),
                    ),
                ),
                P(
                    "3.2",
                    "User access rights to critical systems shall be reviewed at least once every quarter.",
                    obligations=(
                        O(
                            "User access rights to critical systems shall be reviewed at least once every quarter",
                            "TECHNICAL_CONTROL",
                            period="P3M",
                        ),
                    ),
                ),
                P(
                    "3.3",
                    "Privileged access sessions shall be logged and the logs shall be retained for at least 180 days.",
                    obligations=(
                        O(
                            "Privileged access sessions shall be logged and the logs shall be retained for at least 180 days",
                            "RECORD_RETENTION",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "IV",
            "Vulnerability and Patch Management",
            (
                P("4", "", "Patching and Testing"),
                P(
                    "4.1",
                    "Critical security patches shall be applied to all critical systems within 14 "
                    "days of their release.",
                    obligations=(
                        O(
                            "Critical security patches shall be applied to all critical systems within 14 days of their release",
                            "TECHNICAL_CONTROL",
                        ),
                    ),
                ),
                P(
                    "4.2",
                    "The RE shall conduct vulnerability assessment and penetration testing (VA/PT) "
                    "of all critical systems at least once a year.",
                    obligations=(
                        O(
                            "The RE shall conduct vulnerability assessment and penetration testing (VA/PT) of all critical systems at least once a year",
                            "TESTING_AUDIT",
                            period="P1Y",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "V",
            "Business Continuity",
            (
                P("5", "", "Disaster Recovery and Backup"),
                P(
                    "5.1",
                    "The RE shall conduct disaster recovery drills for critical systems at least "
                    "once every six months.",
                    obligations=(
                        O(
                            "The RE shall conduct disaster recovery drills for critical systems at least once every six months",
                            "BUSINESS_CONTINUITY",
                            period="P6M",
                        ),
                    ),
                ),
                P(
                    "5.2",
                    "Backups of critical data shall be taken daily.",
                    obligations=(
                        O("Backups of critical data shall be taken daily", "BUSINESS_CONTINUITY", period="P1D"),
                    ),
                ),
                P(
                    "5.3",
                    "Restoration of backups shall be tested at least once every quarter.",
                    obligations=(
                        O(
                            "Restoration of backups shall be tested at least once every quarter",
                            "BUSINESS_CONTINUITY",
                            period="P3M",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "VI",
            "Incident Management",
            (
                P("6", "", "Incident Reporting"),
                P(
                    "6.1",
                    "The RE shall report every cyber security incident to the Authority within six "
                    "hours of its detection.",
                    obligations=(
                        O(
                            "The RE shall report every cyber security incident to the Authority within six hours of its detection",
                            "INCIDENT_MANAGEMENT",
                        ),
                    ),
                ),
                P(
                    "6.2",
                    "The RE shall conduct a root cause analysis for every critical incident and "
                    "place the report before the Board within 30 days.",
                    obligations=(
                        O(
                            "The RE shall conduct a root cause analysis for every critical incident and place the report before the Board within 30 days",
                            "INCIDENT_MANAGEMENT",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "VII",
            "Assurance",
            (
                P("7", "", "Information Systems Audit"),
                P(
                    "7.1",
                    "The RE shall get an Information Systems audit conducted by an independent "
                    "auditor at least once a year.",
                    obligations=(
                        O(
                            "The RE shall get an Information Systems audit conducted by an independent auditor at least once a year",
                            "TESTING_AUDIT",
                            period="P1Y",
                        ),
                    ),
                ),
            ),
        ),
    ),
    filename="SYN_IT_Governance_Master_Direction_2024.pdf",
)

# ---------------------------------------------------------------------------
# Regulation 3: Data protection retention guidelines (different regulator)
# ---------------------------------------------------------------------------

DPA = RegulationDoc(
    doc_id="SYN-DPA-v1",
    title="Guidelines on Retention and Protection of Personal Data by Financial Institutions",
    regulator="Synthetic Data Protection Authority",
    ref_no="SYN/DPA/2025/02",
    dated="November 5, 2024",
    effective="January 1, 2025",
    preamble=(
        "The Synthetic Data Protection Authority issues these Guidelines to data fiduciaries in "
        "the financial sector. These Guidelines shall come into force on January 1, 2025."
    ),
    chapters=(
        Chapter(
            "I",
            "Preliminary",
            (
                P("1", "", "Scope"),
                P(
                    "1.1",
                    "These Guidelines apply to every bank acting as a data fiduciary in respect of "
                    "personal data of its customers.",
                ),
            ),
        ),
        Chapter(
            "II",
            "Retention and Erasure",
            (
                P("2", "", "Storage Limitation"),
                P(
                    "2.1",
                    "A data fiduciary shall erase the personal data of a customer no later than "
                    "three years after the closure of the account, unless retention is required "
                    "under any other law for the time being in force.",
                    obligations=(
                        O(
                            "A data fiduciary shall erase the personal data of a customer no later than three years after the closure of the account",
                            "DATA_PROTECTION",
                        ),
                    ),
                ),
                P(
                    "2.2",
                    "Personal data contained in system and application logs shall not be retained "
                    "for more than 90 days.",
                    obligations=(
                        O(
                            "Personal data contained in system and application logs shall not be retained for more than 90 days",
                            "DATA_PROTECTION",
                            "MUST_NOT",
                        ),
                    ),
                ),
            ),
        ),
        Chapter(
            "III",
            "Security Safeguards",
            (
                P("3", "", "Breach Notification"),
                P(
                    "3.1",
                    "A data fiduciary shall notify the Authority of any personal data breach "
                    "within 72 hours of becoming aware of it.",
                    obligations=(
                        O(
                            "A data fiduciary shall notify the Authority of any personal data breach within 72 hours of becoming aware of it",
                            "INCIDENT_MANAGEMENT",
                        ),
                    ),
                ),
                P("4", "", "Awareness and Accountability"),
                P(
                    "4.1",
                    "A data fiduciary shall conduct data protection awareness training for all "
                    "employees handling personal data at least once a year.",
                    obligations=(
                        O(
                            "A data fiduciary shall conduct data protection awareness training for all employees handling personal data at least once a year",
                            "TRAINING",
                            period="P1Y",
                        ),
                    ),
                ),
                P(
                    "4.2",
                    "A data fiduciary shall appoint a Data Protection Officer who shall be based in India.",
                    obligations=(
                        O(
                            "A data fiduciary shall appoint a Data Protection Officer who shall be based in India",
                            "GOVERNANCE",
                        ),
                    ),
                ),
            ),
        ),
    ),
    filename="SYN_Data_Retention_Guidelines_2025.pdf",
)

ITG_AMENDMENT_TEXT = (
    "SYN/ITG/2025/11\n"
    "Dated: August 20, 2025\n"
    "Synthetic Regulatory Authority\n"
    "Amendment to the Master Direction on IT Governance (SYN/ITG/2024/03)\n"
    "In paragraph 3.2 of the Master Direction on IT Governance, the words "
    '"at least once every quarter" shall be substituted by "at least once every month '
    'for privileged users and at least once every quarter for other users".\n'
    "This amendment shall come into force with immediate effect."
)

REGULATIONS = (KYC_V1, KYC_V2, ITG, DPA)

# ---------------------------------------------------------------------------
# Synthetic bank: Aravali Bank Ltd
# ---------------------------------------------------------------------------

BANK_NAME = "Aravali Bank Ltd (Synthetic)"
BANK_PROFILE = {
    "entity_type": "scheduled commercial bank",
    "entity_type_aliases": ["scheduled commercial bank", "bank", "regulated entity", "data fiduciary"],
    "jurisdictions": ["IN"],
    "products": ["retail deposits", "corporate accounts", "digital onboarding", "cards"],
    "offers_vcip": True,
    "customer_count": 2_400_000,
}


@dataclass(frozen=True)
class ControlSpec:
    key: str
    name: str
    description: str
    control_type: str
    automation: str
    frequency: str  # ISO-8601 duration or EVENT / CONTINUOUS
    owner: str
    business_unit: str
    systems: tuple[str, ...] = ()
    procedure_ref: str = ""


CONTROLS: tuple[ControlSpec, ...] = (
    ControlSpec(
        "C-KYC-01",
        "KYC policy approval and annual review",
        "The KYC and AML policy is approved by the Board of Directors and placed before the Board for review every year; the Company Secretary records the approval in Board minutes.",
        "PREVENTIVE",
        "MANUAL",
        "P1Y",
        "Chief Compliance Officer",
        "Compliance",
        ("Board Portal",),
        "KYC Policy s.2",
    ),
    ControlSpec(
        "C-KYC-02",
        "Principal Officer designation",
        "The Board designates a Principal Officer for AML; Compliance communicates the name and contact details to the FIU within 30 days of any appointment.",
        "PREVENTIVE",
        "MANUAL",
        "EVENT",
        "Chief Compliance Officer",
        "Compliance",
        (),
        "KYC Policy s.3",
    ),
    ControlSpec(
        "C-KYC-03",
        "CDD at onboarding",
        "The core banking onboarding workflow requires identity verification against officially valid documents before an account number is generated; accounts cannot be activated if CDD is incomplete.",
        "PREVENTIVE",
        "AUTOMATED",
        "EVENT",
        "Head - Retail Operations",
        "Retail Banking",
        ("Core Banking System", "KYC Portal"),
        "KYC Policy s.4",
    ),
    ControlSpec(
        "C-KYC-04",
        "Beneficial owner identification",
        "For legal entity customers, onboarding staff identify beneficial owners holding more than 10% ownership and record them in the KYC Portal; a checker verifies the ownership chart.",
        "PREVENTIVE",
        "MANUAL",
        "EVENT",
        "Head - Corporate Operations",
        "Corporate Banking",
        ("KYC Portal",),
        "KYC Policy s.4.3",
    ),
    ControlSpec(
        "C-KYC-05",
        "Customer risk categorisation review",
        "The AML team reviews the risk categorisation (low, medium, high) of all customers once in six months using the risk scoring engine and documents changes.",
        "DETECTIVE",
        "SEMI_AUTOMATED",
        "P6M",
        "Head - AML",
        "Compliance",
        ("AML Risk Engine",),
        "KYC Policy s.5",
    ),
    ControlSpec(
        "C-KYC-06",
        "Periodic KYC updation tracker",
        "A monthly tracker flags customers due for periodic KYC updation: high risk every two years, medium risk every eight years and low risk every ten years; branches complete updation within 60 days of the flag.",
        "DETECTIVE",
        "AUTOMATED",
        "P1M",
        "Head - Retail Operations",
        "Retail Banking",
        ("Core Banking System",),
        "KYC Policy s.6",
    ),
    ControlSpec(
        "C-KYC-07",
        "V-CIP recording and storage",
        "Every video KYC session is recorded end-to-end and stored in encrypted storage with access restricted to the KYC team.",
        "PREVENTIVE",
        "AUTOMATED",
        "EVENT",
        "Head - Digital Banking",
        "Digital Banking",
        ("V-CIP Platform",),
        "Digital Onboarding SOP",
    ),
    ControlSpec(
        "C-KYC-08",
        "Record retention and archival",
        "Transaction records and customer identification records are archived and retained for five years from the transaction date or from closure of the relationship, whichever is later.",
        "PREVENTIVE",
        "AUTOMATED",
        "CONTINUOUS",
        "Head - IT Operations",
        "Technology",
        ("Archive System",),
        "Records Management SOP",
    ),
    ControlSpec(
        "C-KYC-09",
        "STR filing workflow",
        "Alerts escalated as suspicious are reviewed by the Principal Officer and Suspicious Transaction Reports are filed with the FIU within seven working days of the conclusion; the workflow prevents disclosure to the customer.",
        "DETECTIVE",
        "SEMI_AUTOMATED",
        "EVENT",
        "Principal Officer",
        "Compliance",
        ("AML System",),
        "AML SOP s.7",
    ),
    ControlSpec(
        "C-KYC-10",
        "Monthly CTR submission",
        "Cash Transaction Reports are generated from the core banking system every month and submitted to the FIU before the 15th of the following month.",
        "DETECTIVE",
        "AUTOMATED",
        "P1M",
        "Principal Officer",
        "Compliance",
        ("AML System",),
        "AML SOP s.8",
    ),
    ControlSpec(
        "C-KYC-11",
        "AML transaction monitoring and alert closure",
        "The automated AML system monitors all transactions and generates alerts for unusual patterns; alerts are investigated and closed within 30 days.",
        "DETECTIVE",
        "AUTOMATED",
        "CONTINUOUS",
        "Head - AML",
        "Compliance",
        ("AML System",),
        "AML SOP s.5",
    ),
    ControlSpec(
        "C-KYC-12",
        "Annual KYC/AML training",
        "All frontline staff complete KYC and AML e-learning once a year; completion is tracked in the learning management system.",
        "PREVENTIVE",
        "SEMI_AUTOMATED",
        "P1Y",
        "Head - HR Learning",
        "Human Resources",
        ("LMS",),
        "Training Calendar",
    ),
    ControlSpec(
        "C-KYC-13",
        "Quarterly internal audit of KYC",
        "Internal Audit tests compliance with the KYC policy every quarter on a sample of accounts and reports findings to the Audit Committee.",
        "DETECTIVE",
        "MANUAL",
        "P3M",
        "Chief Internal Auditor",
        "Internal Audit",
        (),
        "Audit Plan",
    ),
    ControlSpec(
        "C-IT-01",
        "MFA for privileged users",
        "Privileged users of critical systems authenticate with multi-factor authentication enforced by the identity provider.",
        "PREVENTIVE",
        "AUTOMATED",
        "CONTINUOUS",
        "CISO",
        "Information Security",
        ("Identity Provider", "PAM"),
        "InfoSec Policy s.4",
    ),
    ControlSpec(
        "C-IT-02",
        "Quarterly user access review",
        "Application owners review user access rights to critical systems every quarter and revoke access that is no longer required.",
        "DETECTIVE",
        "MANUAL",
        "P3M",
        "CISO",
        "Information Security",
        ("IAM",),
        "InfoSec Policy s.5",
    ),
    ControlSpec(
        "C-IT-03",
        "Privileged session logging",
        "All privileged sessions are recorded by the PAM solution and logs are retained for 180 days in the SIEM.",
        "DETECTIVE",
        "AUTOMATED",
        "CONTINUOUS",
        "CISO",
        "Information Security",
        ("PAM", "SIEM"),
        "Logging Standard",
    ),
    ControlSpec(
        "C-IT-04",
        "Patch management",
        "Critical security patches are deployed to critical systems within 30 days of release through the monthly patch cycle.",
        "CORRECTIVE",
        "SEMI_AUTOMATED",
        "P1M",
        "Head - IT Operations",
        "Technology",
        ("Patch Server",),
        "Patch Management SOP",
    ),
    ControlSpec(
        "C-IT-05",
        "Disaster recovery drill",
        "A disaster recovery drill for critical systems is conducted once a year and the results are reported to the IT Strategy Committee.",
        "DETECTIVE",
        "MANUAL",
        "P1Y",
        "Head - IT Operations",
        "Technology",
        ("DR Site",),
        "BCP",
    ),
    ControlSpec(
        "C-IT-06",
        "Annual VA/PT",
        "An external firm performs vulnerability assessment and penetration testing of all critical systems once a year; findings are tracked to closure.",
        "DETECTIVE",
        "MANUAL",
        "P1Y",
        "CISO",
        "Information Security",
        (),
        "VAPT Standard",
    ),
    ControlSpec(
        "C-IT-07",
        "Daily backup and quarterly restore test",
        "Critical data is backed up daily; restoration is tested every quarter for a sample of systems.",
        "CORRECTIVE",
        "AUTOMATED",
        "P1D",
        "Head - IT Operations",
        "Technology",
        ("Backup System",),
        "Backup SOP",
    ),
    ControlSpec(
        "C-IT-08",
        "Cyber incident reporting",
        "The Security Operations Centre classifies incidents and reports cyber security incidents to the regulator within six hours of detection.",
        "CORRECTIVE",
        "MANUAL",
        "EVENT",
        "CISO",
        "Information Security",
        ("SOC Platform",),
        "Incident Response Plan",
    ),
    ControlSpec(
        "C-IT-09",
        "IT Strategy Committee meetings",
        "The IT Strategy Committee of the Board meets every quarter; minutes are recorded by the Company Secretary.",
        "PREVENTIVE",
        "MANUAL",
        "P3M",
        "Company Secretary",
        "Board Secretariat",
        ("Board Portal",),
        "Committee Charter",
    ),
    ControlSpec(
        "C-IT-10",
        "Annual independent IS audit",
        "An independent auditor conducts an information systems audit every year and reports to the Audit Committee.",
        "DETECTIVE",
        "MANUAL",
        "P1Y",
        "Chief Internal Auditor",
        "Internal Audit",
        (),
        "Audit Plan",
    ),
)

# Control tests: (control_key, days_before_today, result, sample, exceptions, notes)
CONTROL_TESTS: tuple[tuple[str, int, str, int, int, str], ...] = (
    ("C-KYC-01", 120, "PASS", 1, 0, "Board minutes confirm approval of the revised KYC policy."),
    ("C-KYC-02", 200, "PASS", 1, 0, "FIU acknowledgement of Principal Officer details on file."),
    ("C-KYC-03", 30, "PASS", 40, 0, "No account activated without completed CDD."),
    ("C-KYC-03", 120, "PASS", 40, 0, "Sample test of onboarding workflow."),
    ("C-KYC-04", 90, "PASS", 15, 0, "Ownership charts present for sampled entities."),
    ("C-KYC-05", 100, "PASS", 1, 0, "Half-yearly risk re-categorisation completed."),
    ("C-KYC-06", 20, "PASS", 25, 1, "One branch completed updation after 75 days."),
    ("C-KYC-07", 60, "PASS", 20, 0, "Recordings retrievable for sampled sessions."),
    ("C-KYC-08", 200, "PASS", 30, 0, "Archive retrieval test successful."),
    ("C-KYC-09", 60, "PASS", 10, 0, "STRs filed within timeline."),
    ("C-KYC-10", 15, "PASS", 1, 0, "CTR submitted on the 12th."),
    ("C-KYC-11", 45, "PARTIAL", 25, 3, "3 of 25 sampled alerts closed after 30 days."),
    ("C-KYC-12", 150, "PASS", 1, 0, "96% completion of annual training."),
    ("C-KYC-13", 275, "PASS", 50, 2, "Last quarterly KYC audit; subsequent quarters not performed."),
    ("C-IT-02", 50, "PASS", 12, 0, "Access reviews completed for 12 critical systems."),
    ("C-IT-03", 80, "PASS", 10, 0, "Session logs available for 180 days."),
    ("C-IT-04", 35, "FAIL", 40, 6, "6 of 40 critical patches applied after 30 days."),
    ("C-IT-05", 300, "PASS", 1, 0, "Annual DR drill completed."),
    ("C-IT-06", 90, "FAIL", 1, 4, "4 critical findings open beyond agreed closure dates."),
    ("C-IT-07", 10, "PASS", 5, 0, "Restoration test successful for 5 systems."),
    ("C-IT-08", 70, "PASS", 3, 0, "Incidents reported within six hours."),
    ("C-IT-09", 40, "PASS", 1, 0, "Quarterly meeting held with quorum."),
    ("C-IT-10", 200, "PASS", 1, 0, "IS audit report issued."),
)


@dataclass(frozen=True)
class EvidenceSpec:
    key: str
    control_key: str
    title: str
    evidence_type: str
    days_ago: int
    authenticity: str
    source_system: str
    body: tuple[str, ...] = field(default_factory=tuple)


EVIDENCE: tuple[EvidenceSpec, ...] = (
    EvidenceSpec(
        "E-01",
        "C-KYC-01",
        "Board minutes extract - KYC policy approval",
        "APPROVAL_RECORD",
        120,
        "SIGNED_REPORT",
        "Board Portal",
        (
            "Extract of the minutes of the meeting of the Board of Directors.",
            "Resolved that the revised Know Your Customer and Anti-Money Laundering Policy, as placed before the Board, is hereby approved.",
            "The Board noted that the policy shall be reviewed annually.",
        ),
    ),
    EvidenceSpec(
        "E-02",
        "C-KYC-13",
        "Internal audit report - KYC compliance",
        "AUDIT_REPORT",
        275,
        "SIGNED_REPORT",
        "Audit Management System",
        (
            "Internal Audit Report: Compliance with the KYC Policy.",
            "Scope: sample of 50 accounts opened during the quarter.",
            "Two exceptions were noted where the risk category was not updated after adverse media.",
            "This is the most recent quarterly KYC audit performed.",
        ),
    ),
    EvidenceSpec(
        "E-03",
        "C-IT-02",
        "Quarterly access review report",
        "TEST_REPORT",
        50,
        "SYSTEM_GENERATED",
        "IAM",
        (
            "Quarterly review of user access rights for 12 critical systems has been completed.",
            "14 access rights that were no longer required were revoked.",
        ),
    ),
    EvidenceSpec(
        "E-04",
        "C-IT-05",
        "Disaster recovery drill report",
        "TEST_REPORT",
        300,
        "SIGNED_REPORT",
        "BCP Office",
        (
            "Annual disaster recovery drill for the core banking system and payment systems.",
            "Recovery time objective achieved for all systems tested.",
        ),
    ),
    EvidenceSpec(
        "E-05",
        "C-IT-06",
        "VA/PT report",
        "TEST_REPORT",
        90,
        "SIGNED_REPORT",
        "External Assessor",
        (
            "Vulnerability assessment and penetration test of 18 critical systems.",
            "Four critical vulnerabilities remain open beyond the agreed closure date.",
        ),
    ),
    EvidenceSpec(
        "E-06",
        "C-IT-04",
        "Patch compliance report",
        "SYSTEM_LOG",
        35,
        "SYSTEM_GENERATED",
        "Patch Server",
        (
            "Critical patches released in the period: 40.",
            "Applied within 14 days: 21. Applied within 30 days: 34. Applied after 30 days: 6.",
        ),
    ),
    EvidenceSpec(
        "E-07",
        "C-KYC-11",
        "AML alert closure MIS",
        "SYSTEM_LOG",
        45,
        "SYSTEM_GENERATED",
        "AML System",
        (
            "Alerts generated in the month: 1,240. Alerts closed within 30 days: 1,151.",
            "Median closure time: 18 days.",
        ),
    ),
    EvidenceSpec(
        "E-08",
        "C-KYC-12",
        "Training completion report",
        "SYSTEM_LOG",
        150,
        "SYSTEM_GENERATED",
        "LMS",
        ("KYC and AML annual e-learning: 96% of frontline staff completed the module.",),
    ),
    EvidenceSpec(
        "E-09",
        "C-KYC-03",
        "Onboarding workflow configuration export",
        "CONFIG_EXPORT",
        30,
        "SYSTEM_GENERATED",
        "Core Banking System",
        (
            "Workflow rule ONB-17: account activation blocked until identity verification status is VERIFIED.",
            "Supported officially valid documents: passport, driving licence, voter identity card, national identity number.",
        ),
    ),
    EvidenceSpec(
        "E-10",
        "C-IT-03",
        "SIEM retention configuration",
        "CONFIG_EXPORT",
        80,
        "SYSTEM_GENERATED",
        "SIEM",
        (
            "Log source PAM-01 retention policy: 180 days hot storage.",
            "Log records include user identifiers and source IP addresses.",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Golden labels beyond obligations
# ---------------------------------------------------------------------------

# (doc_id, section_path) -> expected control keys that satisfy it (FULL or PARTIAL).
GOLD_MAPPINGS: dict[tuple[str, str], tuple[str, ...]] = {
    ("SYN-KYC-v1", "3.1"): ("C-KYC-01",),
    ("SYN-KYC-v1", "3.2"): ("C-KYC-01",),
    ("SYN-KYC-v1", "4.1"): ("C-KYC-02",),
    ("SYN-KYC-v1", "4.2"): ("C-KYC-02",),
    ("SYN-KYC-v1", "5.1"): ("C-KYC-03",),
    ("SYN-KYC-v1", "5.2"): ("C-KYC-03",),
    ("SYN-KYC-v1", "5.3"): ("C-KYC-04",),
    ("SYN-KYC-v1", "6.1"): ("C-KYC-05",),
    ("SYN-KYC-v1", "6.2"): ("C-KYC-05",),
    ("SYN-KYC-v1", "7.1"): ("C-KYC-06",),
    ("SYN-KYC-v1", "7.2"): ("C-KYC-06",),
    ("SYN-KYC-v1", "7.3"): ("C-KYC-06",),
    ("SYN-KYC-v1", "8.2"): ("C-KYC-07",),
    ("SYN-KYC-v1", "9.1"): ("C-KYC-08",),
    ("SYN-KYC-v1", "9.2"): ("C-KYC-08",),
    ("SYN-KYC-v1", "10.1"): ("C-KYC-09",),
    ("SYN-KYC-v1", "10.2"): ("C-KYC-10",),
    ("SYN-KYC-v1", "10.3"): ("C-KYC-09",),
    ("SYN-KYC-v1", "11.1"): ("C-KYC-11",),
    ("SYN-KYC-v1", "11.2"): ("C-KYC-11",),
    ("SYN-KYC-v1", "12.1"): ("C-KYC-12",),
    ("SYN-KYC-v1", "13.1"): ("C-KYC-13",),
    ("SYN-ITG-v1", "2.1"): ("C-IT-09",),
    ("SYN-ITG-v1", "3.1"): ("C-IT-01",),
    ("SYN-ITG-v1", "3.2"): ("C-IT-02",),
    ("SYN-ITG-v1", "3.3"): ("C-IT-03",),
    ("SYN-ITG-v1", "4.1"): ("C-IT-04",),
    ("SYN-ITG-v1", "4.2"): ("C-IT-06",),
    ("SYN-ITG-v1", "5.1"): ("C-IT-05",),
    ("SYN-ITG-v1", "5.2"): ("C-IT-07",),
    ("SYN-ITG-v1", "5.3"): ("C-IT-07",),
    ("SYN-ITG-v1", "6.1"): ("C-IT-08",),
    ("SYN-ITG-v1", "7.1"): ("C-IT-10",),
    ("SYN-DPA-v1", "3.1"): ("C-IT-08",),
    ("SYN-DPA-v1", "4.1"): ("C-KYC-12",),
}

# Seeded gaps (doc_id, section_path, expected gap types - any one counts as detected).
GOLD_GAPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("SYN-KYC-v1", "13.1", ("STALE_EVIDENCE",)),
    ("SYN-KYC-v1", "11.2", ("INEFFECTIVE_CONTROL", "PARTIAL_CONTROL")),
    ("SYN-ITG-v1", "2.2", ("MISSING_CONTROL",)),
    ("SYN-ITG-v1", "3.1", ("MISSING_EVIDENCE",)),
    ("SYN-ITG-v1", "4.1", ("PARTIAL_CONTROL", "INEFFECTIVE_CONTROL")),
    ("SYN-ITG-v1", "4.2", ("INEFFECTIVE_CONTROL",)),
    ("SYN-ITG-v1", "5.1", ("PARTIAL_CONTROL", "INEFFECTIVE_CONTROL", "STALE_EVIDENCE")),
    ("SYN-ITG-v1", "6.2", ("MISSING_CONTROL",)),
    ("SYN-DPA-v1", "2.1", ("MISSING_CONTROL", "CONFLICTING_REGULATION")),
    ("SYN-DPA-v1", "2.2", ("MISSING_CONTROL", "CONFLICTING_REGULATION")),
    ("SYN-DPA-v1", "4.2", ("MISSING_CONTROL",)),
)

# Expected v1 -> v2 changes of the KYC direction.
GOLD_CHANGES: tuple[tuple[str, str, bool], ...] = (
    # (section_path, change_type, is_material)
    ("3.2", "MODIFIED", False),
    ("6.2", "MODIFIED", True),
    ("7.1", "MODIFIED", True),
    ("8.3", "ADDED", True),
    ("11.2", "MODIFIED", True),
)

GOLD_CONFLICTS: tuple[tuple[tuple[str, str], tuple[str, str]], ...] = (
    (("SYN-KYC-v1", "9.2"), ("SYN-DPA-v1", "2.1")),
    (("SYN-ITG-v1", "3.3"), ("SYN-DPA-v1", "2.2")),
)

GOLD_CLUSTERS: tuple[tuple[tuple[str, str], ...], ...] = (
    (("SYN-KYC-v1", "12.1"), ("SYN-DPA-v1", "4.1")),
    (("SYN-ITG-v1", "6.1"), ("SYN-DPA-v1", "3.1")),
)

# Retrieval golden queries -> relevant (doc_id, section_path).
GOLD_RETRIEVAL: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("How often must customer risk categorisation be reviewed?", (("SYN-KYC-v1", "6.2"),)),
    ("periodic updation for high risk customers", (("SYN-KYC-v1", "7.1"),)),
    ("deadline for filing suspicious transaction reports", (("SYN-KYC-v1", "10.1"),)),
    ("CTR submission timeline", (("SYN-KYC-v1", "10.2"),)),
    ("how long to keep customer identification records", (("SYN-KYC-v1", "9.2"),)),
    ("beneficial ownership threshold for legal entities", (("SYN-KYC-v1", "5.3"),)),
    ("video KYC recording requirements", (("SYN-KYC-v1", "8.2"),)),
    ("alert closure timeline for transaction monitoring", (("SYN-KYC-v1", "11.2"),)),
    ("KYC training frequency for frontline staff", (("SYN-KYC-v1", "12.1"),)),
    ("internal audit of KYC compliance frequency", (("SYN-KYC-v1", "13.1"),)),
    ("multi-factor authentication privileged users", (("SYN-ITG-v1", "3.1"),)),
    ("user access review frequency", (("SYN-ITG-v1", "3.2"),)),
    ("log retention period for privileged sessions", (("SYN-ITG-v1", "3.3"),)),
    ("time limit to apply critical patches", (("SYN-ITG-v1", "4.1"),)),
    ("penetration testing frequency", (("SYN-ITG-v1", "4.2"),)),
    ("disaster recovery drill frequency", (("SYN-ITG-v1", "5.1"),)),
    ("backup restoration testing", (("SYN-ITG-v1", "5.3"),)),
    ("report cyber incident to regulator within how many hours", (("SYN-ITG-v1", "6.1"),)),
    ("root cause analysis of critical incidents", (("SYN-ITG-v1", "6.2"),)),
    ("CISO reporting line", (("SYN-ITG-v1", "2.2"),)),
    ("erase personal data after account closure", (("SYN-DPA-v1", "2.1"),)),
    ("personal data in logs retention limit", (("SYN-DPA-v1", "2.2"),)),
    ("personal data breach notification", (("SYN-DPA-v1", "3.1"),)),
    ("data protection officer appointment", (("SYN-DPA-v1", "4.2"),)),
    ("IT strategy committee meeting frequency", (("SYN-ITG-v1", "2.1"),)),
)
