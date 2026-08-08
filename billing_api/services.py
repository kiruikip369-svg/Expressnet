from core.services import shared as _shared
from management.services import billing as _billing
from network.services import provisioning as _provisioning


for _module in (_shared, _billing, _provisioning):
    globals().update(
        {
            name: value
            for name, value in vars(_module).items()
            if not name.startswith("__")
        }
    )

