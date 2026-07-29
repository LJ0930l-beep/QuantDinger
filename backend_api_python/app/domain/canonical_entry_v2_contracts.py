"""Versioned, pure canonical entry V2 contracts; V1 remains untouched."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib, json
from uuid import UUID
from app.domain.decimal_values import Price, Quantity
from app.domain.order_contracts import Actor, OrderAction, RiskEffect
from app.domain.canonical_entry_contracts import EntryActorContext, EntryMode, EntrySource, ExecutionKind, OrderSide, PositionSide, CanonicalEntryRequest, default_entry_mode

CANONICAL_ENTRY_V2 = "canonical-entry-v2"
class CanonicalEntryV2Error(ValueError): pass
class TriggerDirection(str, Enum): AT_OR_ABOVE="AT_OR_ABOVE"; AT_OR_BELOW="AT_OR_BELOW"
class TriggerPriceType(str, Enum): LAST="LAST"; MARK="MARK"; INDEX="INDEX"
class QuantitySemantics(str, Enum): ABSOLUTE="ABSOLUTE"
class CancelTargetKind(str, Enum): ECONOMIC_ORDER_ID="ECONOMIC_ORDER_ID"; CLIENT_ORDER_ID="CLIENT_ORDER_ID"; VENUE_ORDER_ID="VENUE_ORDER_ID"

def _text(value: object, name: str) -> str:
    if not isinstance(value,str) or not value or value != value.strip() or not value.isascii(): raise CanonicalEntryV2Error(f"{name} must be canonical ASCII text")
    return value
def _hash(material: dict) -> str:
    return hashlib.sha256(json.dumps(material, sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")).hexdigest()
def _uuid(value: UUID|str, name: str) -> str:
    try: return str(UUID(str(value))).lower()
    except (TypeError, ValueError, AttributeError) as exc: raise CanonicalEntryV2Error(f"{name} must be UUID") from exc

@dataclass(frozen=True, slots=True)
class CanonicalEconomicIntentV2:
    side: OrderSide|None=None; quantity: Quantity|None=None; quantity_semantics: QuantitySemantics|None=None
    execution_kind: ExecutionKind|None=None; limit_price: Price|None=None; trigger_price: Price|None=None
    trigger_direction: TriggerDirection|None=None; trigger_price_type: TriggerPriceType|None=None
    reduce_only: bool=False; position_side: PositionSide=PositionSide.NET
    cancel_target_kind: CancelTargetKind|None=None; cancel_target_id: str|None=None
    target_position_id: str|None=None; close_quantity: Quantity|None=None; close_all: bool=False
    def __post_init__(self):
        if not isinstance(self.reduce_only,bool) or not isinstance(self.close_all,bool): raise CanonicalEntryV2Error("reduce_only and close_all must be bool")
        for n,t in (("side",OrderSide),("execution_kind",ExecutionKind),("quantity_semantics",QuantitySemantics),("trigger_direction",TriggerDirection),("trigger_price_type",TriggerPriceType),("position_side",PositionSide),("cancel_target_kind",CancelTargetKind)):
            if (v:=getattr(self,n)) is not None and not isinstance(v,t): raise CanonicalEntryV2Error(f"{n} must use typed enum")
        for n in ("quantity","close_quantity"):
            if (v:=getattr(self,n)) is not None and (not isinstance(v,Quantity) or v.to_decimal()<=0): raise CanonicalEntryV2Error(f"{n} must be positive Quantity")
        for n in ("limit_price","trigger_price"):
            if (v:=getattr(self,n)) is not None and not isinstance(v,Price): raise CanonicalEntryV2Error(f"{n} must be Price")
        for n in ("cancel_target_id","target_position_id"):
            if (v:=getattr(self,n)) is not None: object.__setattr__(self,n,_text(v,n))
        if self.cancel_target_id is not None and self.cancel_target_kind is CancelTargetKind.ECONOMIC_ORDER_ID:
            object.__setattr__(self,"cancel_target_id",_uuid(self.cancel_target_id,"cancel_target_id"))
    def validate(self, action: OrderAction) -> None:
        if not isinstance(action,OrderAction): raise CanonicalEntryV2Error("action must be typed")
        cancel = action is OrderAction.CANCEL
        if cancel:
            if self.cancel_target_kind is None or self.cancel_target_id is None: raise CanonicalEntryV2Error("cancel target required")
            if self.position_side is not PositionSide.NET or any(v is not None for v in (self.side,self.quantity,self.quantity_semantics,self.execution_kind,self.limit_price,self.trigger_price,self.trigger_direction,self.trigger_price_type,self.target_position_id,self.close_quantity)) or self.reduce_only or self.close_all: raise CanonicalEntryV2Error("cancel cannot carry execution facts")
            return
        if self.cancel_target_kind is not None or self.cancel_target_id is not None: raise CanonicalEntryV2Error("non-cancel cannot carry cancel target")
        if self.side is None or self.execution_kind is None: raise CanonicalEntryV2Error("side and execution required")
        stops=self.execution_kind in (ExecutionKind.STOP_MARKET,ExecutionKind.STOP_LIMIT)
        limits=self.execution_kind in (ExecutionKind.LIMIT,ExecutionKind.STOP_LIMIT)
        if (self.limit_price is not None)!=limits or (self.trigger_price is not None)!=stops or (self.trigger_direction is not None)!=stops or (self.trigger_price_type is not None)!=stops: raise CanonicalEntryV2Error("execution price facts are incomplete")
        reducing=action in (OrderAction.REDUCE,OrderAction.CLOSE,OrderAction.EMERGENCY_CLOSE,OrderAction.PROTECTION)
        if reducing:
            if not self.reduce_only or self.target_position_id is None or self.close_all == (self.close_quantity is not None) or self.quantity is not None or self.quantity_semantics is not None or self.cancel_target_id is not None: raise CanonicalEntryV2Error("invalid reducing facts")
        elif self.reduce_only or self.quantity is None or self.quantity_semantics is not QuantitySemantics.ABSOLUTE or self.target_position_id is not None or self.close_quantity is not None or self.close_all or self.cancel_target_id is not None: raise CanonicalEntryV2Error("invalid increasing facts")
    def facts(self):
        return {"side":None if self.side is None else self.side.value,"quantity":None if self.quantity is None else self.quantity.to_string(),"quantity_semantics":None if self.quantity_semantics is None else self.quantity_semantics.value,"execution_kind":None if self.execution_kind is None else self.execution_kind.value,"limit_price":None if self.limit_price is None else self.limit_price.to_string(),"trigger_price":None if self.trigger_price is None else self.trigger_price.to_string(),"trigger_direction":None if self.trigger_direction is None else self.trigger_direction.value,"trigger_price_type":None if self.trigger_price_type is None else self.trigger_price_type.value,"reduce_only":self.reduce_only,"position_side":self.position_side.value,"cancel_target_kind":None if self.cancel_target_kind is None else self.cancel_target_kind.value,"cancel_target_id":self.cancel_target_id,"target_position_id":self.target_position_id,"close_quantity":None if self.close_quantity is None else self.close_quantity.to_string(),"close_all":self.close_all}

@dataclass(frozen=True, slots=True)
class CanonicalEntryRequestV2:
    tenant_id:int; credential_id:int; account_scope:str; instrument_id:str; market_type:str; action:OrderAction; economic_intent:CanonicalEconomicIntentV2; actor:EntryActorContext; risk_effect:RiskEffect; idempotency_key:str; correlation_id:str; occurred_at:datetime; mode:EntryMode|None=None
    economic_fingerprint:str=field(init=False); request_fingerprint:str=field(init=False)
    def __post_init__(self):
        if (isinstance(self.tenant_id,bool) or not isinstance(self.tenant_id,int) or self.tenant_id<=0 or isinstance(self.credential_id,bool) or not isinstance(self.credential_id,int) or self.credential_id<=0 or not isinstance(self.action,OrderAction) or not isinstance(self.actor,EntryActorContext) or not isinstance(self.economic_intent,CanonicalEconomicIntentV2)): raise CanonicalEntryV2Error("invalid V2 scope")
        for n,upper in (("account_scope",False),("instrument_id",True),("market_type",False)):
            v=_text(getattr(self,n),n); object.__setattr__(self,n,v.upper() if upper else v.lower() if n=="market_type" else v)
        expected=RiskEffect.NEUTRAL if self.action is OrderAction.CANCEL else RiskEffect.INCREASE_RISK if self.action in (OrderAction.OPEN,OrderAction.INCREASE) else RiskEffect.REDUCE_RISK
        if self.risk_effect is not expected: raise CanonicalEntryV2Error("risk effect mismatch")
        if self.action is OrderAction.PROTECTION and (self.actor.actor_type is not Actor.PROTECTION or self.actor.entry_source is not EntrySource.PROTECTION): raise CanonicalEntryV2Error("protection actor required")
        if self.actor.entry_source is EntrySource.PROTECTION and (self.action not in (OrderAction.REDUCE,OrderAction.CLOSE,OrderAction.EMERGENCY_CLOSE,OrderAction.PROTECTION) or self.risk_effect is not RiskEffect.REDUCE_RISK): raise CanonicalEntryV2Error("protection source may only reduce risk")
        object.__setattr__(self,"idempotency_key",_text(self.idempotency_key,"idempotency_key")); object.__setattr__(self,"correlation_id",_text(self.correlation_id,"correlation_id"))
        if not isinstance(self.occurred_at,datetime) or self.occurred_at.tzinfo is None or self.occurred_at.utcoffset()!=timezone.utc.utcoffset(self.occurred_at): raise CanonicalEntryV2Error("occurred_at must be UTC")
        object.__setattr__(self,"occurred_at",self.occurred_at.astimezone(timezone.utc)); mode=default_entry_mode(self.actor.entry_source) if self.mode is None else self.mode
        if not isinstance(mode,EntryMode): raise CanonicalEntryV2Error("mode must be typed")
        object.__setattr__(self,"mode",mode)
        self.economic_intent.validate(self.action)
        object.__setattr__(self,"economic_fingerprint",_hash(self.economic_material()))
        object.__setattr__(self,"request_fingerprint",_hash({"version":CANONICAL_ENTRY_V2,"economic_fingerprint":self.economic_fingerprint,"actor_type":self.actor.actor_type.value,"actor_id":self.actor.actor_id,"source":self.actor.entry_source.value,"idempotency_key":self.idempotency_key,"correlation_id":self.correlation_id,"occurred_at":self.occurred_at.isoformat(),"mode":self.mode.value}))
    def economic_material(self): return {"version":CANONICAL_ENTRY_V2,"tenant_id":self.tenant_id,"credential_id":self.credential_id,"account_scope":self.account_scope,"instrument_id":self.instrument_id,"market_type":self.market_type,"action":self.action.value,"risk_effect":self.risk_effect.value,"economic_intent":self.economic_intent.facts()}

@dataclass(frozen=True, slots=True)
class EconomicOrderSubject:
    economic_order_id: UUID|str
    def __post_init__(self): object.__setattr__(self,"economic_order_id",_uuid(self.economic_order_id,"economic_order_id"))
@dataclass(frozen=True, slots=True)
class CancelTargetSubject:
    cancel_target_kind: CancelTargetKind; cancel_target_id:str
    def __post_init__(self):
        if not isinstance(self.cancel_target_kind,CancelTargetKind): raise CanonicalEntryV2Error("cancel_target_kind must be typed")
        value=_uuid(self.cancel_target_id,"cancel_target_id") if self.cancel_target_kind is CancelTargetKind.ECONOMIC_ORDER_ID else _text(self.cancel_target_id,"cancel_target_id")
        object.__setattr__(self,"cancel_target_id",value)
@dataclass(frozen=True, slots=True)
class DurableEntryGraphV2:
    command_id: UUID|str; specification: CanonicalEntryRequestV2; subject: EconomicOrderSubject|CancelTargetSubject
    def __post_init__(self):
        object.__setattr__(self,"command_id",_uuid(self.command_id,"command_id"))
        if not isinstance(self.specification,CanonicalEntryRequestV2) or not isinstance(self.subject,(EconomicOrderSubject,CancelTargetSubject)): raise CanonicalEntryV2Error("graph requires typed specification and subject")
        if self.specification.action is OrderAction.CANCEL:
            if not isinstance(self.subject,CancelTargetSubject): raise CanonicalEntryV2Error("cancel requires cancel subject")
            if (self.specification.economic_intent.cancel_target_kind,self.specification.economic_intent.cancel_target_id)!=(self.subject.cancel_target_kind,self.subject.cancel_target_id): raise CanonicalEntryV2Error("cancel subject mismatch")
        elif not isinstance(self.subject,EconomicOrderSubject): raise CanonicalEntryV2Error("non-cancel requires economic order subject")

def convert_v1_non_stop(request: CanonicalEntryRequest) -> CanonicalEntryRequestV2:
    if not isinstance(request,CanonicalEntryRequest): raise CanonicalEntryV2Error("V1 request required")
    intent=request.economic_intent
    if request.action is OrderAction.CANCEL or intent.execution_kind in (ExecutionKind.STOP_MARKET,ExecutionKind.STOP_LIMIT): raise CanonicalEntryV2Error("V1 CANCEL/STOP requires explicit conversion")
    return CanonicalEntryRequestV2(request.tenant_id,request.credential_id,request.account_scope,request.instrument_id,request.market_type,request.action,CanonicalEconomicIntentV2(intent.side,intent.quantity,QuantitySemantics.ABSOLUTE if intent.quantity else None,intent.execution_kind,intent.limit_price,None,None,None,intent.reduce_only,intent.position_side,None,None,intent.target_position_id,intent.close_quantity,intent.close_all),request.actor,request.risk_effect,request.idempotency_key,request.correlation_id,request.occurred_at,request.mode)
def convert_v1_cancel(request: CanonicalEntryRequest, *, cancel_target_kind: CancelTargetKind) -> CanonicalEntryRequestV2:
    if not isinstance(request,CanonicalEntryRequest) or request.action is not OrderAction.CANCEL or not isinstance(cancel_target_kind,CancelTargetKind): raise CanonicalEntryV2Error("explicit V1 cancel target kind required")
    return CanonicalEntryRequestV2(request.tenant_id,request.credential_id,request.account_scope,request.instrument_id,request.market_type,request.action,CanonicalEconomicIntentV2(cancel_target_kind=cancel_target_kind,cancel_target_id=request.economic_intent.cancel_target_id),request.actor,request.risk_effect,request.idempotency_key,request.correlation_id,request.occurred_at,request.mode)
