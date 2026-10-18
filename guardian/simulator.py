"""Background traffic for the bank console.

Real banks score thousands of payments a second; the console would look
empty with only the phone demo. The simulator streams payments from other
(synthetic) customers through the same engine so the console shows the
system working at scale, and it keeps feeding the mule accounts so their
receiver signals grow during the demo.
"""

import random

import numpy as np

from .generator import sample_one

FIRST = ["Ramesh", "Sunita", "Aarav", "Kavita", "Imran", "Lakshmi", "Harpreet", "Joseph", "Meena",
         "Vikram", "Anjali", "Rahul", "Farida", "Gopal", "Shalini", "Arjun", "Nirmala", "Suresh",
         "Pooja", "Abdul", "Deepa", "Manoj", "Rekha", "Kiran", "Sanjay", "Usha", "Tarun", "Geeta"]
LAST = ["Iyer", "Verma", "Khan", "Patel", "Reddy", "Singh", "Das", "Nair", "Joshi", "Mehta", "Rao",
        "Chatterjee", "Kulkarni", "Fernandes", "Pillai", "Saxena", "Bose", "Gill", "Yadav", "Mishra"]
CITIES = ["Pune", "Jaipur", "Kochi", "Indore", "Lucknow", "Bhopal", "Chennai", "Patna", "Nagpur",
          "Surat", "Mysuru", "Guwahati", "Dehradun", "Vadodara", "Ranchi", "Delhi", "Kolkata"]
SHOPS = ["Sai Medicals", "Annapurna Sweets", "City Electricals", "Fresh Basket", "Om Tours",
         "Patel Hardware", "Royal Tailors", "Green Valley School", "Apollo Pharmacy Counter",
         "Krishna Dairy", "Metro Mobiles", "Landlord", "Sharma Tuition", "Gas Agency"]

# a busier scam mix than real life, so a three minute demo shows all cases
MIX = [
    ("everyday", 0.60), ("big_legit", 0.12), ("family_emergency", 0.06), ("anxious_senior", 0.06),
    ("new_merchant", 0.05), ("digital_arrest", 0.05), ("relative_clone", 0.03), ("task_scam", 0.03),
]
SCAMS = {"digital_arrest", "relative_clone", "task_scam"}


class Simulator:
    def __init__(self, bank, seed=None):
        self.bank = bank
        self.rng = np.random.default_rng(seed)
        self.py = random.Random(seed)

    def _persona(self):
        if any(r.flag for r in self.bank.receivers.values()) and self.py.random() < 0.12:
            return "digital_arrest"
        r = self.py.random()
        acc = 0
        for name, share in MIX:
            acc += share
            if r <= acc:
                return name
        return MIX[0][0]

    def next(self):
        persona = self._persona()
        f = sample_one(self.rng, persona)
        age = int(f["sender_age"])
        avg = self.py.choice([800, 1500, 2500, 4000, 6500, 9000])
        amount = int(max(50, f["amount_ratio"] * avg) // 10 * 10)
        sender = {
            "id": f"S-{self.py.randint(10000, 99999)}",
            "name": f"{self.py.choice(FIRST)} {self.py.choice(LAST)}",
            "age": age,
            "city": self.py.choice(CITIES),
        }
        receiver = None
        if persona in SCAMS:
            mules = [r for r in self.bank.receivers.values() if r.kind == "mule" and not r.reported]
            hot = [r for r in self.bank.receivers.values() if r.flag and not r.reported]
            # a flagged mule is still active and still calling people
            if hot and self.py.random() < 0.7:
                receiver = self.py.choice(hot)
                if self.py.random() < 0.5:
                    # a well-coached victim: calm, no stress signals, which
                    # the model alone would let through
                    f = sample_one(self.rng, "big_legit")
                    f["new_payee"], f["payee_history"] = 1.0, 0.0
                    f.update(receiver.features())
                    amount = int(max(20000, f["amount_ratio"] * avg) // 10 * 10)
            else:
                receiver = self.py.choice(mules)
            payee = {"name": receiver.name, "account": receiver.account}
        else:
            name = self.py.choice(SHOPS) if self.py.random() < 0.6 else \
                f"{self.py.choice(FIRST)} {self.py.choice(LAST)}"
            payee = {"name": name, "account": str(self.py.randint(10**13, 10**14 - 1))}
        return persona, f, sender, payee, amount, receiver
