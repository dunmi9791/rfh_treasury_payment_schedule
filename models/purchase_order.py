from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare

from .res_company import PROCUREMENT_DIRECTOR_GROUP
PO_APPROVAL_ACTIVITY_XMLID = 'rfh_treasury_payment_schedule.mail_activity_po_approval'
PO_APPROVAL_TEMPLATE_XMLID = 'rfh_treasury_payment_schedule.mail_template_po_approval_request'
APPROVAL_PROTECTED_FIELDS = {
    'rfh_approval_state',
    'rfh_approval_user_id',
    'rfh_approval_date',
    'rfh_approval_note',
    'rfh_approval_grandfathered',
}
COMMERCIAL_LINE_FIELDS = ('product_id', 'product_qty', 'price_unit', 'discount')


class PurchaseOrder(models.Model):
    """Purchase approval gates Treasury payment. It does not gate confirmation.

    New orders start in ``to_approve``. A buyer requests approval, or the
    Procurement Director approves directly. Until the state is ``approved``,
    Treasury cannot queue, approve, or pay the order or a vendor bill linked
    to it. Confirming the order (draft -> purchase) is unchanged, so goods can
    still be ordered while payment waits.

    Orders that were already confirmed when this flow was installed or upgraded
    are grandfathered by ``_rfh_grandfather_confirmed_orders``. A later change
    to product, quantity, unit price, or discount clears that exemption.
    """

    _inherit = ['purchase.order', 'rfh.treasury.payment.origin.mixin']
    _name = 'purchase.order'

    treasury_payment_state = fields.Selection([
        ('not_queued', 'Not Queued'),
        ('queued', 'Queued'),
        ('scheduled', 'Scheduled'),
        ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid'),
    ], string='Treasury Status', default='not_queued', tracking=True)

    treasury_payment_count = fields.Integer(
        string='Treasury Payments',
        compute='_compute_treasury_payment_count',
    )

    rfh_approval_state = fields.Selection([
        ('to_approve', 'To Approve'),
        ('requested', 'Approval Requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Procurement Approval', default='to_approve', required=True,
        copy=False, tracking=True, index=True)
    rfh_approval_user_id = fields.Many2one(
        'res.users',
        string='Approved / Rejected By',
        copy=False,
        tracking=True,
        readonly=True,
    )
    rfh_approval_date = fields.Datetime(
        string='Approval Date',
        copy=False,
        tracking=True,
        readonly=True,
    )
    rfh_approval_note = fields.Text(
        string='Approval Note',
        copy=False,
        readonly=True,
    )
    rfh_approval_grandfathered = fields.Boolean(
        string='Grandfathered on Upgrade',
        copy=False,
        readonly=True,
        help=(
            'Set for purchase orders that were already confirmed when this '
            'approval flow was installed or upgraded. Treasury may pay them '
            'without a new Procurement Director approval until the order lines change.'
        ),
    )
    rfh_can_approve = fields.Boolean(
        compute='_compute_rfh_can_approve',
        string='Current User Can Approve',
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    def _compute_treasury_payment_count(self):
        # sudo: buyers who are not Treasury users can open the purchase order.
        Schedule = self.env['rfh.treasury.payment.schedule'].sudo()
        for po in self:
            po.treasury_payment_count = Schedule.search_count([
                ('source_model', '=', 'purchase.order'),
                ('source_res_id', '=', po.id),
            ])

    @api.depends('company_id.rfh_po_approver_id')
    @api.depends_context('uid')
    def _compute_rfh_can_approve(self):
        user = self.env.user
        for order in self:
            order.rfh_can_approve = order._rfh_user_can_approve(user)

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    def _rfh_write_approval(self, vals):
        return self.with_context(rfh_po_approval_internal=True).write(vals)

    def write(self, vals):
        if (
            APPROVAL_PROTECTED_FIELDS.intersection(vals)
            and not self.env.context.get('rfh_po_approval_internal')
        ):
            raise AccessError(_(
                'Purchase approval can only be changed with the approval actions.'
            ))
        return super().write(vals)

    def _rfh_is_approved_for_payment(self):
        self.ensure_one()
        return self.rfh_approval_state == 'approved'

    def _rfh_user_can_approve(self, user):
        """Designated company user wins. Otherwise any Procurement Director."""
        self.ensure_one()
        designated = self.company_id.rfh_po_approver_id
        if designated:
            return user == designated
        return user.has_group(PROCUREMENT_DIRECTOR_GROUP)

    def _rfh_check_current_user_can_approve(self):
        self.ensure_one()
        if self._rfh_user_can_approve(self.env.user):
            return
        designated = self.company_id.rfh_po_approver_id
        if designated:
            raise AccessError(_(
                'Only %(user)s, the Procurement Director for %(company)s, '
                'can approve or reject this purchase order.',
                user=designated.display_name,
                company=self.company_id.display_name,
            ))
        raise AccessError(_(
            'Only a member of the Procurement Director group can approve '
            'or reject this purchase order.'
        ))

    def _rfh_check_can_request_approval(self):
        self.ensure_one()
        user = self.env.user
        if not (
            user.has_group('purchase.group_purchase_user')
            or user.has_group(PROCUREMENT_DIRECTOR_GROUP)
        ):
            raise AccessError(_('Only purchase users can request purchase order approval.'))
        if self.state == 'cancel':
            raise UserError(_('Cancelled purchase orders cannot be submitted for approval.'))
        if self.rfh_approval_state == 'approved':
            raise UserError(_('Purchase order %s is already approved.', self.name))
        if self.rfh_approval_state not in ('to_approve', 'rejected'):
            raise UserError(_(
                'Purchase order %(name)s cannot be submitted for approval from status %(status)s.',
                name=self.name,
                status=dict(self._fields['rfh_approval_state'].selection).get(self.rfh_approval_state),
            ))

    def _rfh_approval_recipient_users(self):
        self.ensure_one()
        designated = self.company_id.rfh_po_approver_id
        if designated:
            if not designated.active or designated.share:
                raise UserError(_(
                    'The Procurement Director configured for %(company)s is not an active internal user.',
                    company=self.company_id.display_name,
                ))
            return designated
        group = self.env.ref(PROCUREMENT_DIRECTOR_GROUP)
        users = group.users.filtered(lambda user: user.active and not user.share)
        if not users:
            raise UserError(_(
                'No Procurement Director is configured for %(company)s. '
                'Set one in Purchase settings, or add a user to the Procurement Director group.',
                company=self.company_id.display_name,
            ))
        return users

    def _rfh_notify_approvers(self, users):
        self.ensure_one()
        emailed = users.filtered(lambda user: user.email)
        if not emailed:
            names = ', '.join(users.mapped('name'))
            raise UserError(_(
                'The Procurement Director has no email address, so the approval '
                'request cannot be sent: %s',
                names,
            ))
        template = self.env.ref(PO_APPROVAL_TEMPLATE_XMLID).sudo()
        email_from = (
            self.env.user.email_formatted
            or self.company_id.email_formatted
            or 'noreply@riders.local'
        )
        self.message_subscribe(partner_ids=users.partner_id.ids)
        for user in users:
            self.activity_schedule(
                PO_APPROVAL_ACTIVITY_XMLID,
                user_id=user.id,
                summary=_('Approve purchase order %s', self.name),
                note=_(
                    'Approve or reject %(order)s for %(vendor)s before Treasury can pay it.',
                    order=self.name,
                    vendor=self.partner_id.display_name,
                ),
            )
        for user in emailed:
            template.with_context(rfh_approver_email=user.email).send_mail(
                self.id,
                force_send=False,
                email_values={
                    'email_to': user.email,
                    'email_from': email_from,
                },
            )
        skipped = users - emailed
        body = _(
            'Approval requested from %(names)s.',
            names=', '.join(emailed.mapped('name')),
        )
        if skipped:
            body = _(
                '%(body)s No email address for %(skipped)s; an activity was still assigned.',
                body=body,
                skipped=', '.join(skipped.mapped('name')),
            )
        self.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _rfh_close_approval_activities(self):
        self.activity_feedback([PO_APPROVAL_ACTIVITY_XMLID])

    def action_rfh_request_approval(self):
        for order in self:
            order._rfh_check_can_request_approval()
            recipients = order._rfh_approval_recipient_users()
            order._rfh_write_approval({
                'rfh_approval_state': 'requested',
                'rfh_approval_user_id': False,
                'rfh_approval_date': False,
                'rfh_approval_note': False,
                'rfh_approval_grandfathered': False,
            })
            order._rfh_notify_approvers(recipients)
        return True

    def action_rfh_approve(self):
        for order in self:
            order._rfh_check_current_user_can_approve()
            if order.state == 'cancel':
                raise UserError(_('Cancelled purchase orders cannot be approved.'))
            if order.rfh_approval_state == 'approved':
                raise UserError(_('Purchase order %s is already approved.', order.name))
            if order.rfh_approval_state not in ('to_approve', 'requested'):
                raise UserError(_(
                    'Purchase order %(name)s cannot be approved from status %(status)s.',
                    name=order.name,
                    status=order.rfh_approval_state,
                ))
            order._rfh_write_approval({
                'rfh_approval_state': 'approved',
                'rfh_approval_user_id': self.env.user.id,
                'rfh_approval_date': fields.Datetime.now(),
                'rfh_approval_grandfathered': False,
                'rfh_approval_note': False,
            })
            order._rfh_close_approval_activities()
            order.message_post(
                body=_(
                    'Purchase order approved by %s. Treasury may now queue and pay it.',
                    self.env.user.display_name,
                ),
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_rfh_reject(self):
        self.ensure_one()
        self._rfh_check_current_user_can_approve()
        if self.state == 'cancel':
            raise UserError(_('Cancelled purchase orders cannot be rejected.'))
        if self.rfh_approval_state not in ('to_approve', 'requested'):
            raise UserError(_(
                'Purchase order %(name)s cannot be rejected from status %(status)s.',
                name=self.name,
                status=self.rfh_approval_state,
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject Purchase Order'),
            'res_model': 'rfh.po.approval.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _rfh_apply_rejection(self, reason):
        self.ensure_one()
        self._rfh_check_current_user_can_approve()
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_('A rejection reason is required.'))
        if self.state == 'cancel':
            raise UserError(_('Cancelled purchase orders cannot be rejected.'))
        if self.rfh_approval_state not in ('to_approve', 'requested'):
            raise UserError(_(
                'Purchase order %(name)s cannot be rejected from status %(status)s.',
                name=self.name,
                status=self.rfh_approval_state,
            ))
        self._rfh_write_approval({
            'rfh_approval_state': 'rejected',
            'rfh_approval_user_id': self.env.user.id,
            'rfh_approval_date': fields.Datetime.now(),
            'rfh_approval_grandfathered': False,
            'rfh_approval_note': reason,
        })
        self._rfh_close_approval_activities()
        self.message_post(
            body=_(
                'Purchase order rejected by %(user)s. %(reason)s',
                user=self.env.user.display_name,
                reason=reason,
            ),
            subtype_xmlid='mail.mt_note',
        )
        return True

    def _rfh_reset_approval_for_change(self):
        orders = self.filtered(
            lambda order: order.rfh_approval_state in ('approved', 'requested')
        )
        if not orders:
            return
        orders._rfh_write_approval({
            'rfh_approval_state': 'to_approve',
            'rfh_approval_user_id': False,
            'rfh_approval_date': False,
            'rfh_approval_note': False,
            'rfh_approval_grandfathered': False,
        })
        orders._rfh_close_approval_activities()
        for order in orders:
            order.message_post(
                body=_(
                    'Purchase approval was cleared because the order lines changed '
                    '(product, quantity, price, or discount). Request approval again '
                    'before Treasury can pay this order.'
                ),
                subtype_xmlid='mail.mt_note',
            )

    def _rfh_grandfather_confirmed_orders(self):
        """Approve orders that were already confirmed before this flow existed.

        Install (``post_init_hook``) and upgrade (``migrations/18.0.1.1.0``)
        both call this. Only records in this recordset are considered.

        * ``purchase`` and ``done`` orders still in ``to_approve`` are marked
          approved, with ``rfh_approval_grandfathered`` set and no approving
          user. Treasury can keep paying them.
        * Draft, sent, and cancelled orders stay in the new flow. They are not
          payable yet, so this does not stop a live payment.
        * Requested, rejected, and already approved orders are left as they are.

        Running it again does nothing to orders already processed. Changing
        the product, quantity, unit price, or discount later clears the
        exemption and blocks payment until a Procurement Director approves.
        """
        payable = self.filtered(
            lambda order: order.state in ('purchase', 'done')
            and order.rfh_approval_state == 'to_approve'
            and not order.rfh_approval_grandfathered
        )
        if not payable:
            return payable
        note = _(
            'Grandfathered when purchase approval was introduced '
            '(rfh_treasury_payment_schedule 18.0.1.1.0). This order was already '
            'confirmed, so Treasury may pay it without a new Procurement Director '
            'approval. Changing the product, quantity, price, or discount removes '
            'this exemption.'
        )
        payable._rfh_write_approval({
            'rfh_approval_state': 'approved',
            'rfh_approval_grandfathered': True,
            'rfh_approval_date': fields.Datetime.now(),
            'rfh_approval_user_id': False,
            'rfh_approval_note': note,
        })
        for order in payable:
            order.message_post(body=note, subtype_xmlid='mail.mt_note')
        return payable

    def _rfh_check_can_queue_for_treasury(self):
        self.ensure_one()
        if self.state not in ('purchase', 'done'):
            raise UserError(
                _('Only confirmed or done purchase orders can be queued for Treasury payment.')
            )
        if not self._rfh_is_approved_for_payment():
            raise UserError(_(
                'Purchase order %(name)s is not approved. Treasury cannot queue, '
                'approve, or pay it until the Procurement Director approves it.',
                name=self.display_name,
            ))

    # ------------------------------------------------------------------
    # Mixin implementation
    # ------------------------------------------------------------------

    def _prepare_treasury_payment_schedule_vals(self):
        self.ensure_one()
        self._rfh_check_can_queue_for_treasury()
        due = self.date_planned.date() if self.date_planned else fields.Date.today()
        return {
            'source_model': 'purchase.order',
            'source_res_id': self.id,
            'source_reference': self.name,
            'partner_id': self.partner_id.id,
            'amount': self.amount_total,
            'currency_id': self.currency_id.id,
            'company_id': self.company_id.id,
            'due_date': due,
            'description': _('Payment for Purchase Order %s', self.name),
            'state': 'queued',
        }

    def action_create_treasury_payment_schedule(self):
        self.ensure_one()
        self._rfh_check_can_queue_for_treasury()
        result = super().action_create_treasury_payment_schedule()
        self.treasury_payment_state = 'queued'
        return result

    def action_treasury_payment_completed(self, payment_schedule):
        self.ensure_one()
        self.message_post(
            body=_('Treasury payment completed: %s', payment_schedule.name)
        )
        self.treasury_payment_state = 'paid'

    # ------------------------------------------------------------------
    # Smart button action
    # ------------------------------------------------------------------

    def action_view_treasury_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Treasury Payments'),
            'res_model': 'rfh.treasury.payment.schedule',
            'view_mode': 'list,form',
            'domain': [
                ('source_model', '=', 'purchase.order'),
                ('source_res_id', '=', self.id),
            ],
            'context': {
                'default_source_model': 'purchase.order',
                'default_source_res_id': self.id,
            },
        }


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _rfh_orders_awaiting_reset(self):
        return self.filtered(lambda line: not line.display_type).order_id.filtered(
            lambda order: order.rfh_approval_state in ('approved', 'requested')
        )

    def _rfh_commercial_snapshot(self):
        self.ensure_one()
        return {
            'product_id': self.product_id.id,
            'product_qty': self.product_qty,
            'price_unit': self.price_unit,
            'discount': self.discount or 0.0,
        }

    def _rfh_commercial_values_changed(self, before, fields_written):
        self.ensure_one()
        if 'product_id' in fields_written and before['product_id'] != self.product_id.id:
            return True
        qty_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        if 'product_qty' in fields_written and float_compare(
            before['product_qty'], self.product_qty, precision_digits=qty_digits,
        ) != 0:
            return True
        price_digits = self.order_id.currency_id.decimal_places or 2
        if 'price_unit' in fields_written and float_compare(
            before['price_unit'], self.price_unit, precision_digits=price_digits,
        ) != 0:
            return True
        if 'discount' in fields_written and float_compare(
            before['discount'], self.discount or 0.0, precision_digits=2,
        ) != 0:
            return True
        return False

    @api.model_create_multi
    def create(self, vals_list):
        order_ids = {
            vals['order_id']
            for vals in vals_list
            if vals.get('order_id') and not vals.get('display_type') and (
                vals.get('product_id') or vals.get('is_downpayment')
            )
        }
        lines = super().create(vals_list)
        if order_ids:
            self.env['purchase.order'].browse(list(order_ids))._rfh_reset_approval_for_change()
        return lines

    def write(self, vals):
        watched = [name for name in COMMERCIAL_LINE_FIELDS if name in vals]
        snapshots = {}
        if watched:
            for line in self:
                if line.display_type or line.order_id.rfh_approval_state not in ('approved', 'requested'):
                    continue
                snapshots[line.id] = line._rfh_commercial_snapshot()
        res = super().write(vals)
        if snapshots:
            changed = self.env['purchase.order']
            for line in self.browse(list(snapshots)):
                if line._rfh_commercial_values_changed(snapshots[line.id], watched):
                    changed |= line.order_id
            changed._rfh_reset_approval_for_change()
        return res

    def unlink(self):
        orders = self._rfh_orders_awaiting_reset()
        res = super().unlink()
        if orders:
            orders._rfh_reset_approval_for_change()
        return res
