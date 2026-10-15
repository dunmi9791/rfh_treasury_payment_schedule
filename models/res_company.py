from odoo import api, fields, models, _
from odoo.exceptions import UserError

PROCUREMENT_DIRECTOR_GROUP = 'rfh_treasury_payment_schedule.group_procurement_director'


class ResCompany(models.Model):
    _inherit = 'res.company'

    rfh_po_approver_id = fields.Many2one(
        'res.users',
        string='Procurement Director',
        domain="[('share', '=', False)]",
        help=(
            'Internal user who approves purchase orders before Treasury can pay them. '
            'When this is empty, any member of the Procurement Director group may approve. '
            'Saving a user here also adds them to that group. '
            'This is not Odoo\'s Purchase Order Approval setting.'
        ),
    )

    def write(self, vals):
        if vals.get('rfh_po_approver_id'):
            users = self.env['res.users'].browse(vals['rfh_po_approver_id'])
            if any(user.share for user in users):
                raise UserError(_('The Procurement Director must be an internal user.'))
        res = super().write(vals)
        if vals.get('rfh_po_approver_id'):
            self._rfh_grant_procurement_director(self.mapped('rfh_po_approver_id'))
        return res

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        approvers = companies.mapped('rfh_po_approver_id')
        if approvers:
            if any(user.share for user in approvers):
                raise UserError(_('The Procurement Director must be an internal user.'))
            companies._rfh_grant_procurement_director(approvers)
        return companies

    def _rfh_grant_procurement_director(self, users):
        group = self.env.ref(PROCUREMENT_DIRECTOR_GROUP, raise_if_not_found=False)
        if not group or not users:
            return
        missing = users.filtered(lambda user: group not in user.groups_id)
        if missing:
            missing.sudo().write({'groups_id': [(4, group.id)]})
