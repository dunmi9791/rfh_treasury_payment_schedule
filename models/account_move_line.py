from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def action_register_payment(self, ctx=None):
        """Pay from journal items uses the same gate as the bill Pay button."""
        self.move_id._rfh_assert_direct_payment_allowed()
        return super().action_register_payment(ctx=ctx)
