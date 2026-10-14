from odoo import models


class AccountPaymentRegister(models.TransientModel):
    """Register Payment cannot settle a vendor bill tied to an unapproved PO.

    The bill Pay button is blocked as well. Creating the payment here is the
    server-side check, so calling the wizard directly cannot skip it.
    """

    _inherit = 'account.payment.register'

    def _create_payments(self):
        self.line_ids.move_id._rfh_assert_direct_payment_allowed()
        return super()._create_payments()
