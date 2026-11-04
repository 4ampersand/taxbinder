"""The invented household behind the sample set.

Every name, number and amount is made up. SSNs are 900-00-xxxx (the SSA never issues area 900
or group 00) and EINs start with 00 (a prefix the IRS never assigns).
"""

from __future__ import annotations

from dataclasses import dataclass

ADDRESS = ("41 Larkspur Lane", "Hollis Ferry, NY 13999")


@dataclass(frozen=True)
class Person:
    id: str  # taxpayer, spouse, dependent
    name: str
    ssn: str


PEOPLE = {
    p.id: p
    for p in [
        Person("taxpayer", "Avery Quill", "900-00-0101"),
        Person("spouse", "Jordan Quill", "900-00-0102"),
        Person("dependent", "Rowan Quill", "900-00-0103"),
    ]
}
TRUST = Person("trust", "Quill Family Trust", "00-0000900")  # a filer with its own vault
FILERS = {"household": "Avery and Jordan Quill", "trust": TRUST.name}


@dataclass(frozen=True)
class Issuer:
    id: str  # the slug an institution record would get
    name: str  # as printed on the form
    ein: str
    kind: str  # Institution.kind


ISSUERS = {
    i.id: i
    for i in [
        Issuer("brightwater-analytics", "Brightwater Analytics Inc.", "00-0000101", "employer"),
        Issuer("sorrel-valley-health", "Sorrel Valley Health Partners", "00-0000102", "employer"),
        Issuer("tamarack-savings-bank", "Tamarack Savings Bank", "00-0000103", "bank"),
        Issuer("larkmoor-securities", "Larkmoor Securities LLC", "00-0000104", "brokerage"),
        Issuer("quarry-hill-funds", "Quarry Hill Funds", "00-0000105", "brokerage"),
        Issuer("pellucid-retirement", "Pellucid Retirement Services", "00-0000106", "brokerage"),
        Issuer("gannet-home-lending", "Gannet Home Lending", "00-0000107", "bank"),
        Issuer("social-security", "Social Security Administration", "", "government"),
        Issuer("ridgeway-college-savings", "Ridgeway College Savings Plan", "00-0000109", "other"),
        Issuer("willowbend-hsa", "Willowbend HSA Bank", "00-0000110", "bank"),
        Issuer("kestrel-media", "Kestrel Media Group", "00-0000111", "other"),
        Issuer("ambergate-property", "Ambergate Property Management", "00-0000112", "other"),
        Issuer("literacy-circle", "Hollis Ferry Literacy Circle", "00-0000113", "charity"),
        Issuer("town-of-hollis-ferry", "Town of Hollis Ferry", "", "government"),
        Issuer("hollis-quill-legacy-trust", "Hollis Quill Legacy Trust", "00-0000114", "other"),
    ]
}

# The 14 entities on the 2024 return's Schedule E Part II: (id, printed name, EIN, P or S).
ENTITIES = [
    ("alderpoint-timber", "Alderpoint Timber Fund LP", "00-0000201", "P"),
    ("bluefen-apartments", "Bluefen Apartments LP", "00-0000202", "P"),
    ("cinder-creek-energy", "Cinder Creek Energy Partners LP", "00-0000203", "P"),
    ("dovecote-storage", "Dovecote Storage Fund LP", "00-0000204", "P"),
    ("elmstead-medical", "Elmstead Medical Office LP", "00-0000205", "P"),
    ("foxglove-ventures", "Foxglove Ventures II LP", "00-0000206", "P"),
    ("granite-wren", "Granite Wren Holdings LLC", "00-0000207", "P"),
    ("heronsgate-industrial", "Heronsgate Industrial LP", "00-0000208", "P"),
    ("ironleaf-agriculture", "Ironleaf Agriculture LP", "00-0000209", "P"),  # fiscal year
    ("juniper-mesa-housing", "Juniper Mesa Housing LP", "00-0000210", "P"),
    ("kittiwake-infrastructure", "Kittiwake Infrastructure LP", "00-0000211", "P"),
    ("lantern-bay-hotels", "Lantern Bay Hotels LLC", "00-0000212", "P"),
    ("mossgrove-lending", "Mossgrove Lending Fund LP", "00-0000213", "P"),
    ("nettlefield-design", "Nettlefield Design Inc.", "00-0000214", "S"),  # an S-corporation
]
for _id, _name, _ein, _ps in ENTITIES:
    ISSUERS[_id] = Issuer(_id, _name, _ein, "fund_sponsor")

FISCAL = "ironleaf-agriculture"  # tax year July 1 – June 30
MISSING_2024 = ["kittiwake-infrastructure", "lantern-bay-hotels"]  # on the return, no K-1 yet
