import logging

from . import models
from . import wizard
from . import controllers

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Grandfather purchase orders that already exist on first install.

    Upgrades from 18.0.1.0.0 use migrations/18.0.1.1.0/post-migrate.py instead.
    Both call the same method. Confirmed and locked orders stay payable.
    Draft and sent orders enter the new approval flow.
    """
    orders = env['purchase.order'].search([('state', 'in', ('purchase', 'done'))])
    grandfathered = orders._rfh_grandfather_confirmed_orders()
    _logger.info(
        'PO approval install: grandfathered %s confirmed purchase order(s).',
        len(grandfathered),
    )
