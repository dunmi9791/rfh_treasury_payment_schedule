"""Grandfather purchase orders that were already confirmed before approval existed.

Runs only when upgrading an existing database to 18.0.1.1.0. A fresh install
uses post_init_hook, which calls the same method. Draft and sent orders are
not payable yet, so they stay in the new flow and need a Procurement Director
before Treasury can pay them. Confirmed and locked orders are marked approved
so live payments are not blocked. See purchase.order._rfh_grandfather_confirmed_orders.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['purchase.order'].search([('state', 'in', ('purchase', 'done'))])
    grandfathered = orders._rfh_grandfather_confirmed_orders()
    _logger.info(
        'PO approval upgrade from %s: grandfathered %s confirmed purchase order(s). '
        'Draft and sent orders were left waiting for a Procurement Director.',
        version,
        len(grandfathered),
    )
