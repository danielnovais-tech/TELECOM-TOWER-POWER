# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
from enum import Enum


class Band(str, Enum):
    VALUE_0 = "700MHz"
    VALUE_1 = "850MHz"
    VALUE_2 = "900MHz"
    VALUE_3 = "1800MHz"
    VALUE_4 = "2100MHz"
    VALUE_5 = "2600MHz"
    VALUE_6 = "3500MHz"

    def __str__(self) -> str:
        return str(self.value)
