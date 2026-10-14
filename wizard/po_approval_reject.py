from odoo import fields, models, _
from odoo.exceptions import UserError


class RfhPoApprovalRejectWizard(models.TransientModel):
    _name = 'rfh.po.approval.reject.wizard'
    _description = 'Reject Purchase Order Approval'

    order_id = fields.Many2one(
        'purchase.order',
        string='Purchase Order',
        required=True,
        readonly=True,
    )
    reason = fields.Text(string='Reason', required=True)

    def action_reject(self):
        self.ensure_one()
        if not self.order_id:
            raise UserError(_('Choose the purchase order to reject.'))
        self.order_id._rfh_apply_rejection(self.reason)
        return {'type': 'ir.actions.act_window_close'}
