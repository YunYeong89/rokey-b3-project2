/* ================================================================
   API 계층: 서버(web_server.py)가 있으면 서버 사용,
   없으면(파일로 열거나 미리보기) 브라우저 안에서 데모 시뮬레이션
   ================================================================ */
let LOW = 1;  // 보충 필요 기준 (브라우저 데모에서는 products.json 의 low_stock 값으로 바뀜)
const Demo = (() => {
  // 상품 정보 원본은 pickup_web/products.json. 불러오기에 실패하면(파일로 직접 열기 등) 아래 기본값 사용
  const FALLBACK = {
    low_stock:1,
    products:[
      {"id":"tuna_can","name":"참치캔","emoji":"🐟","price":2500,"stock":3,"warehouse":5,"category":"식품","aisle":"canned meat seafood","robot":true,"shelf":"lower","fragile":false,"deformable":false,"registered":true},
      {"id":"spam_can","name":"스팸 통조림","emoji":"🥫","price":4500,"stock":3,"warehouse":5,"category":"식품","aisle":"canned meat seafood","robot":true,"shelf":"lower","fragile":false,"deformable":false,"registered":true},
      {"id":"milk_pack","name":"소형 우유팩","emoji":"🥛","price":1200,"stock":3,"warehouse":5,"category":"음료","aisle":"milk","robot":true,"shelf":"lower","fragile":false,"deformable":false,"registered":true},
      {"id":"soap_box","name":"비누 상자","emoji":"🧼","price":2000,"stock":3,"warehouse":5,"category":"생활용품","aisle":"soap","robot":true,"shelf":"lower","fragile":false,"deformable":false,"registered":true},
      {"id":"choco_box","name":"초콜릿","emoji":"🍫","price":3000,"stock":3,"warehouse":5,"category":"간식","aisle":"candy chocolate","robot":true,"shelf":"upper","fragile":false,"deformable":false,"registered":true},
      {"id":"snack_bag","name":"과자","emoji":"🍿","price":1500,"stock":3,"warehouse":5,"category":"간식","aisle":"chips pretzels","robot":true,"shelf":"upper","fragile":false,"deformable":true,"registered":true},
      {"id":"mini_tissue","name":"미니 티슈","emoji":"🧻","price":800,"stock":3,"warehouse":5,"category":"생활용품","aisle":"paper goods","robot":true,"shelf":"upper","fragile":false,"deformable":true,"registered":true},
      {"id":"egg_pack","name":"계란 모형 포장 용기","emoji":"🥚","price":3500,"stock":3,"warehouse":5,"category":"식품","aisle":"eggs","robot":true,"shelf":"upper","fragile":true,"deformable":false,"registered":true},
      {"id":"new_item","name":"신규 실험용 상품","emoji":"📦","price":1000,"stock":2,"warehouse":5,"category":"기타","aisle":null,"robot":true,"shelf":"upper","fragile":false,"deformable":false,"registered":false},
      {"id":"cup_ramen","name":"컵라면","emoji":"🍜","price":1300,"stock":10,"warehouse":10,"category":"식품","aisle":"instant foods","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"cola","name":"콜라","emoji":"🥤","price":2000,"stock":10,"warehouse":10,"category":"음료","aisle":"soft drinks","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"water","name":"생수","emoji":"💧","price":1000,"stock":10,"warehouse":10,"category":"음료","aisle":"water seltzer sparkling water","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"can_coffee","name":"캔커피","emoji":"☕","price":1500,"stock":10,"warehouse":10,"category":"음료","aisle":"coffee","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"yogurt","name":"요구르트","emoji":"🥣","price":1800,"stock":10,"warehouse":10,"category":"음료","aisle":"yogurt","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"bread","name":"식빵","emoji":"🍞","price":3000,"stock":10,"warehouse":10,"category":"식품","aisle":"bread","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"cookie","name":"쿠키","emoji":"🍪","price":2000,"stock":10,"warehouse":10,"category":"간식","aisle":"cookies cakes","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"ice_cream","name":"아이스크림","emoji":"🍦","price":2000,"stock":10,"warehouse":10,"category":"간식","aisle":"ice cream ice","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"cereal","name":"시리얼","emoji":"🌾","price":4500,"stock":10,"warehouse":10,"category":"식품","aisle":"cereal","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"nuts","name":"견과류","emoji":"🥜","price":3500,"stock":10,"warehouse":10,"category":"간식","aisle":"nuts seeds dried fruit","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"toothpaste","name":"치약","emoji":"🪥","price":3000,"stock":10,"warehouse":10,"category":"생활용품","aisle":"oral hygiene","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"juice","name":"주스","emoji":"🧃","price":1800,"stock":10,"warehouse":10,"category":"음료","aisle":"juice nectars","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"sausage","name":"소시지","emoji":"🌭","price":2500,"stock":10,"warehouse":10,"category":"식품","aisle":"hot dogs bacon sausage","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"cracker","name":"크래커","emoji":"🍘","price":1500,"stock":10,"warehouse":10,"category":"간식","aisle":"crackers","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"lunchbox","name":"도시락","emoji":"🍱","price":4800,"stock":10,"warehouse":10,"category":"식품","aisle":"prepared meals","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"tortilla_chips","name":"나초·토르티야 칩","emoji":"🌮","price":2000,"stock":10,"warehouse":10,"category":"간식","aisle":"chips pretzels","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"dark_chocolate","name":"다크초콜릿","emoji":"🍫","price":3500,"stock":10,"warehouse":10,"category":"간식","aisle":"candy chocolate","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"strawberry_yogurt","name":"딸기 요구르트","emoji":"🍓","price":1900,"stock":10,"warehouse":10,"category":"음료","aisle":"yogurt","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true},
      {"id":"orange_soda","name":"오렌지 탄산음료","emoji":"🍊","price":2000,"stock":10,"warehouse":10,"category":"음료","aisle":"soft drinks","robot":false,"shelf":null,"fragile":false,"deformable":false,"registered":true}
    ],
  };
  // our_recs.json 요약 (불러오기 실패 시 사용)
  const FALLBACK_RECS = {
      "tuna_can":{"popularity":0.00759,"substitute":["spam_can"],"recommend":[]},
      "spam_can":{"popularity":0.00759,"substitute":["tuna_can"],"recommend":[]},
      "milk_pack":{"popularity":0.14553,"substitute":[],"recommend":[{"id":"cereal","lift":1.344},{"id":"yogurt","lift":1.31}]},
      "soap_box":{"popularity":0.00774,"substitute":[],"recommend":[{"id":"toothpaste","lift":4.53},{"id":"mini_tissue","lift":2.266}]},
      "choco_box":{"popularity":0.04199,"substitute":[],"recommend":[{"id":"cookie","lift":2.025},{"id":"nuts","lift":1.89},{"id":"snack_bag","lift":1.587},{"id":"cola","lift":1.537},{"id":"cracker","lift":1.396}]},
      "snack_bag":{"popularity":0.07464,"substitute":[],"recommend":[{"id":"cookie","lift":1.838},{"id":"choco_box","lift":1.587},{"id":"cracker","lift":1.548}]},
      "mini_tissue":{"popularity":0.03604,"substitute":[],"recommend":[{"id":"toothpaste","lift":3.885},{"id":"soap_box","lift":2.266},{"id":"cola","lift":1.364},{"id":"water","lift":1.32},{"id":"can_coffee","lift":1.23}]},
      "egg_pack":{"popularity":0.05782,"substitute":[],"recommend":[{"id":"sausage","lift":1.442},{"id":"bread","lift":1.248}]},
      "cup_ramen":{"popularity":0.01329,"substitute":[],"recommend":[]},
      "cola":{"popularity":0.06616,"substitute":[],"recommend":[{"id":"water","lift":1.688},{"id":"choco_box","lift":1.537},{"id":"cookie","lift":1.517},{"id":"mini_tissue","lift":1.364},{"id":"juice","lift":1.351}]},
      "water":{"popularity":0.14054,"substitute":[],"recommend":[{"id":"cola","lift":1.688},{"id":"mini_tissue","lift":1.32},{"id":"can_coffee","lift":1.217}]},
      "can_coffee":{"popularity":0.03305,"substitute":[],"recommend":[{"id":"mini_tissue","lift":1.23},{"id":"water","lift":1.217}]},
      "yogurt":{"popularity":0.12951,"substitute":[],"recommend":[{"id":"milk_pack","lift":1.31}]},
      "bread":{"popularity":0.0664,"substitute":[],"recommend":[{"id":"egg_pack","lift":1.248}]},
      "cookie":{"popularity":0.02889,"substitute":[],"recommend":[{"id":"choco_box","lift":2.025},{"id":"cracker","lift":2.009},{"id":"snack_bag","lift":1.838},{"id":"cola","lift":1.517},{"id":"ice_cream","lift":1.426}]},
      "ice_cream":{"popularity":0.06097,"substitute":[],"recommend":[{"id":"cookie","lift":1.426},{"id":"cola","lift":1.204}]},
      "cereal":{"popularity":0.03811,"substitute":[],"recommend":[{"id":"milk_pack","lift":1.344},{"id":"cracker","lift":1.295},{"id":"cookie","lift":1.235}]},
      "nuts":{"popularity":0.04114,"substitute":[],"recommend":[{"id":"choco_box","lift":1.89},{"id":"cracker","lift":1.262}]},
      "toothpaste":{"popularity":0.00848,"substitute":[],"recommend":[{"id":"soap_box","lift":4.53},{"id":"mini_tissue","lift":3.885}]},
      "juice":{"popularity":0.04665,"substitute":[],"recommend":[{"id":"cola","lift":1.351}]},
      "sausage":{"popularity":0.03129,"substitute":[],"recommend":[{"id":"egg_pack","lift":1.442}]},
      "cracker":{"popularity":0.04526,"substitute":[],"recommend":[{"id":"cookie","lift":2.009},{"id":"snack_bag","lift":1.548},{"id":"choco_box","lift":1.396},{"id":"cereal","lift":1.295},{"id":"nuts","lift":1.262}]},
      "lunchbox":{"popularity":0.01322,"substitute":[],"recommend":[]}
  };
  let P=[], RECS={}, ready=null;
  const getJSON=(url,fb)=>fetch(url).then(r=>{if(!r.ok) throw new Error(r.statusText);return r.json()}).catch(()=>fb);
  const load=()=>ready||(ready=Promise.all([getJSON("/data/products.json",FALLBACK),getJSON("/data/recommend/our_recs.json",FALLBACK_RECS)])
    .then(([c,r])=>{P=c.products.map(p=>({...p}));RECS=r||{};LOW=c.low_stock??LOW}));
  /* 브라우저 데모용 간이 추천 (서버 없이 열 때만 사용). 실제 추천(R / S / C / 인기)은 recommender.py.
     데모: 첫 화면은 인기 상품 Top 3, 장바구니는 our_recs.json 의 코너 lift (예전 방식) */
  const RANK={strong:2,weak:1,popular:0};
  const pop=id=>(RECS[id]||{}).popularity||0, stockOf=id=>(get(id)||{}).stock||0;
  const levelOf=e=>!e?"popular":e.level?e.level:e.lift==null?"popular":e.lift>=1.2?"strong":e.lift>=1.1?"weak":"popular";
  const score=(id,e)=>(RANK[levelOf(e)]||0)*100+(e&&e.lift!=null?e.lift:pop(id));
  const reasonOf=(lv,lift)=>lv==="strong"?`같이 사는 비율 ${lift.toFixed(1)}배`:lv==="weak"?"이런 상품은 어때요?":"인기 상품";
  const item=(id,level,reason)=>{const p=get(id);return{id,name:p.name,emoji:p.emoji,reason,level,algo:level==="popular"?"popular":"C"}};
  function recHome(k=3){
    return P.filter(p=>p.id!=="new_item"&&p.stock>0&&pop(p.id)>0).sort((a,b)=>score(b.id)-score(a.id)).slice(0,k).map(p=>item(p.id,"popular","인기 상품"));
  }
  function recBasket(basket,k=3){
    const entries=basket.flatMap(b=>(RECS[b]||{}).recommend||[])
      .concat(P.filter(p=>p.id!=="new_item"&&pop(p.id)>0).map(p=>({id:p.id,lift:null,level:"popular"})));
    const best={};
    for(const e of entries){const id=e.id;if(!get(id)||basket.includes(id)||stockOf(id)<=0) continue;
      const s=score(id,e);if(!best[id]||s>best[id].s) best[id]={s,e}}
    const ranked=Object.entries(best).sort((a,b)=>b[1].s-a[1].s), win={};
    for(const [id] of ranked){const a=get(id).aisle;if(a&&(!(a in win)||stockOf(id)>stockOf(win[a]))) win[a]=id}
    return ranked.filter(([id])=>{const a=get(id).aisle;return !a||win[a]===id}).slice(0,k)
      .map(([id,{e}])=>{const lv=levelOf(e);return item(id,lv,reasonOf(lv,e.lift))});
  }
  const recSub=id=>((RECS[id]||{}).substitute||[]).filter(s=>get(s)&&s!==id&&stockOf(s)>0);
  let status={state:"idle"}, vision={}, vtime=null, alerts=[], oid=1, resolveChoice=null, orders={};  // orders: 데모 픽업 기록
  const get=id=>P.find(p=>p.id===id), wait=ms=>new Promise(r=>setTimeout(r,ms));
  const now=()=>new Date().toTimeString().slice(0,8);
  const alert=(level,text)=>{alerts.unshift({time:now(),level,text});alerts=alerts.slice(0,30)};
  async function run(id,all){
    // robot: true 상품만 로봇이 담고, 가상 상품은 직원 준비 (staff_items)
    let items={}, staff={};
    for(const [k,v] of Object.entries(all)) if(v>0) (get(k).robot?items:staff)[k]=v;
    let total=Object.values(items).reduce((a,b)=>a+b,0);
    status={order_id:id,state:"checking",message:"매대에서 재고를 확인하고 있어요",done:0,total};
    await wait(1500);
    const seen=Object.fromEntries(Object.keys(items).map(pid=>[pid,vision[pid]??get(pid).stock]));
    Object.assign(vision,seen);vtime=now();
    for(const [pid,want] of Object.entries({...items})){
      const have=seen[pid];
      if(have<want){
        status={order_id:id,state:"need_choice",message:"재고가 부족해요",shortage:{id:pid,want,have},substitute:recSub(pid)[0]||null,done:0,total};
        const c=await new Promise(r=>resolveChoice=r);
        if(c.choice==="cancel"){status={order_id:id,state:"cancelled",message:"주문이 취소되었어요"};
          orders[id]={order_id:id,status:"cancelled",message:status.message,picked:{},staff_items:{}};return}
        items[pid]=have;
        if(c.choice==="substitute"&&c.substitute){const dst=get(c.substitute).robot?items:staff;dst[c.substitute]=(dst[c.substitute]||0)+(want-have)}
        for(const k in items) if(!items[k]) delete items[k];
        total=Object.values(items).reduce((a,b)=>a+b,0);
      }
    }
    const picked={}; let done=0;
    for(const [pid,n] of Object.entries(items)) for(let i=0;i<n;i++){
      status={order_id:id,state:"picking",current:pid,done,total,picked:{...picked},message:"상품을 집어서 바구니에 담고 있어요"};
      await wait(1800); picked[pid]=(picked[pid]||0)+1; done++;
    }
    for(const [pid,n] of Object.entries({...picked,...staff})){const p=get(pid);p.stock=Math.max(0,p.stock-n);if(pid in vision) vision[pid]=Math.max(0,vision[pid]-n);
      if(p.stock<=LOW) p.warehouse>0?alert("warn",`${p.name} 진열 보충 필요 (진열 ${p.stock}개 / 창고 ${p.warehouse}개)`)
        :alert("error",`${p.name} 창고 재고 없음 - 발주 필요 (진열 ${p.stock}개)`)}
    status={order_id:id,state:"done",done,total,picked,staff_items:staff,message:"담기 완료! 픽업대에서 가져가세요"};
    const t=Date.now()/1000;  // 데모: 픽업 번호만 흉내 (QR·키오스크는 서버가 있어야 동작)
    orders[id]={order_id:id,status:"ready_for_pickup",pickup_code:"A-"+String(id).padStart(3,"0"),check:"0000",
      items:all,picked,staff_items:staff,ready_at:t,expires_at:t+30*60};
  }
  return {
    products:async()=>{await load();return {products:P,mode:"demo"}},
    order:async items=>{if(["checking","picking","need_choice"].includes(status.state)) throw new Error("로봇이 다른 주문을 처리 중이에요");
      for(const [k,n] of Object.entries(items)){const p=get(k);if(!p.robot&&n>p.stock) throw new Error(`${p.name} 재고가 부족해요 (남은 ${p.stock}개)`)}
      const id=oid++; run(id,{...items}); return {order_id:id,client_secret:"demo"}},
    status:async()=>status,
    myOrder:async id=>{const o=orders[id], now=Date.now()/1000;
      if(!o) return {order_id:id,status:"in_progress",robot:status.order_id===id?status:{state:"checking"},now};
      if(o.status==="ready_for_pickup"&&now>=o.expires_at) o.status="expired";
      return {...o,now}},
    qrUrl:()=>null,
    pickups:async()=>{const now=Date.now()/1000;
      return {now,expire_min:30,orders:Object.values(orders).filter(o=>o.status==="ready_for_pickup"||o.status==="expired")
        .map(o=>({...o,status:now>=o.expires_at?"expired":o.status,count:Object.values({...o.picked,...o.staff_items}).reduce((a,b)=>a+b,0)}))}},
    choice:async c=>{resolveChoice&&resolveChoice(c);resolveChoice=null;return{ok:true}},
    recommend:async basket=>{await load();return {items:basket.length?recBasket(basket):[],title:"함께 구매하면 좋은 상품"}},
    home:async()=>{await load();return {items:recHome(),mode:"popular",title:"인기 상품 추천",n_orders:0}},
    event:async()=>({ok:true}),
    customer:async cid=>({customer_id:cid,n_orders:0,items:[]}),  // 브라우저 데모는 구매 이력 없음
    inventory:async()=>({rows:P.map(p=>{const v=vision[p.id];return{...p,vision:v,state:v!=null&&v!==p.stock?"mismatch":p.stock<=LOW?(p.warehouse>0?"low":"order"):"ok"}}),
      vision_time:vtime,alerts,robot:status}),
    scan:async()=>{setTimeout(()=>{const R=P.filter(p=>p.robot);  // 카메라는 실물 매대만
      vision=Object.fromEntries(R.map(p=>[p.id,p.stock]));
      if(Math.random()<.4){const p=R[Math.floor(Math.random()*R.length)];vision[p.id]=Math.max(0,p.stock-1)}
      vtime=now();R.forEach(p=>{if(vision[p.id]!==p.stock) alert("error",`${p.name} 불일치: 진열 ${p.stock}개 / 카메라 ${vision[p.id]}개`)})},1500);return{ok:true}},
    restock:async(id,qty,mode="move")=>{const p=get(id), sg=n=>(n>0?"+":"")+n; let text;
      if(mode==="move"){const n=qty>0?Math.min(qty,p.warehouse):Math.max(qty,-p.stock);
        if(!n) throw new Error(qty>0?"창고 재고가 없어요":"진열 재고가 없어요");
        p.warehouse-=n;p.stock+=n;text=`${p.name} 창고 -> 진열 ${sg(n)}개 (진열 ${p.stock}개 / 창고 ${p.warehouse}개)`}
      else if(mode==="receive"){p.warehouse=Math.max(0,p.warehouse+qty);text=`${p.name} 창고 입고 ${sg(qty)}개 (창고 ${p.warehouse}개)`}
      else {p.stock=Math.max(0,p.stock+qty);text=`${p.name} 진열 재고 ${sg(qty)}개 수정 (진열 ${p.stock}개)`}
      delete vision[id];alert("info",text);return{ok:true}},
  };
})();

const Server = {
  async j(url,opt){const r=await fetch(url,opt);const d=await r.json().catch(()=>({}));
    if(!r.ok){const e=new Error(d.detail||d.message||r.statusText);e.status=r.status;throw e}return d},
  post(url,body){return this.j(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})})},
  products(){return this.j("/api/products")},
  order(items,customer_id,rec_attrib){return this.post("/api/order",{items,customer_id,rec_attrib})},
  status(){return this.j("/api/status")},
  choice(c){return this.post("/api/choice",c)},
  recommend(b,cid){return this.j(`/api/recommend?basket=${b.join(",")}&customer_id=${encodeURIComponent(cid||"")}`)},
  home(cid,b){return this.j(`/api/recommend/home?customer_id=${encodeURIComponent(cid||"")}&basket=${(b||[]).join(",")}`)},
  event(e){return this.post("/api/rec_event",e).catch(()=>{})},  // 기록 실패는 무시
  customer(cid){return this.j("/api/customer?customer_id="+encodeURIComponent(cid))},
  inventory(){return this.j("/api/inventory")},
  scan(){return this.post("/api/scan")},
  restock(id,qty,mode="move"){return this.post("/api/restock",{id,qty,mode})},
  myOrder(id,secret){return this.j(`/api/pickup/order/${id}?secret=${encodeURIComponent(secret)}`)},
  qrUrl(id,secret){return `/api/pickup/qr/${id}.png?secret=${encodeURIComponent(secret)}`},
  pickups(){return this.j("/api/pickups")},
};
let API = Server;

/* ================================================================ UI */
const $=s=>document.querySelector(s);
const won=n=>n.toLocaleString("ko-KR")+"원";
let products=[], cart={}, polling=null, lastState=null, cat="전체", recoKey=null;
const CATS=["전체","식품","음료","간식","생활용품"];
const pmap=()=>Object.fromEntries(products.map(p=>[p.id,p]));
// 장바구니에 담긴 상품 id, 개수 합, 금액 합
function cartSummary(){const m=pmap(), ids=Object.keys(cart).filter(k=>cart[k]>0&&m[k]);
  return {m,ids,n:ids.reduce((a,k)=>a+cart[k],0),t:ids.reduce((a,k)=>a+cart[k]*m[k].price,0)}}

function toast(t){const e=$("#toast");e.textContent=t;e.classList.add("on");clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove("on"),2200)}

async function loadProducts(){
  try{const d=await API.products();products=d.products;
    $("#mode").textContent=d.mode==="robot"?"🤖 로봇 연결됨":d.mode==="mock"?"서버 · 데모 로봇":"브라우저 데모";
  }catch(e){
    if(API===Server){API=Demo;return loadProducts()}
    toast(e.message)
  }
  renderGrid();
  renderHome();
}

/* ---------------- 고객번호 (시연용 간단 로그인: 비밀번호 없이 번호만, localStorage 에 기억) */
const CUST_KEY="rokey_customer";
let cust=(()=>{try{return localStorage.getItem(CUST_KEY)||null}catch{return null}})();
const CUST_RE=/^[0-9A-Za-z가-힣_-]{1,20}$/;
function setCust(id){cust=id||null;try{id?localStorage.setItem(CUST_KEY,id):localStorage.removeItem(CUST_KEY)}catch{}
  updateCustBtn();renderHome()}
function updateCustBtn(){$("#custBtn").textContent=cust?`👤 ${cust}`:"👤 로그인"}

async function renderCustSheet(){
  let info=null;
  if(cust){try{info=await API.customer(cust)}catch{}}
  const top=(info?.items||[]).slice(0,5), m=pmap();
  $("#custSheet").innerHTML=`<div class="sheet-h"><div class="st-title">👤 ${cust?`고객 ${cust}`:"고객번호 입력"}</div>
      <button class="x" data-cu="close" aria-label="닫기">✕</button></div>
    ${cust?`<div class="st-msg" style="text-align:left;margin:4px 2px 0">구매 완료 주문 <b>${info?.n_orders??0}번</b>
        ${info?.n_orders?"· 첫 화면에 평소 자주 사는 상품을 보여 드려요":"· 아직 이력이 없어 인기 상품을 보여 드려요"}</div>
      ${top.length?`<ul class="chist">${top.map(it=>`<li><span>${m[it.id]?m[it.id].emoji+" ":""}${it.name}</span>
        <small>${info.n_orders}번 중 ${it.orders}번</small></li>`).join("")}</ul>`:""}
      <button class="btn ghost" style="width:100%;margin-top:14px" data-cu="logout">다른 번호로 바꾸기 / 로그아웃</button>`
    :`<div class="st-msg" style="text-align:left;margin:4px 2px 0">고객번호를 입력하면 주문할 때마다 구매 이력이 쌓이고,
        첫 화면에 <b>평소 자주 구매하시는 상품</b>을 추천해 드려요.</div>
      <input class="cinput" id="custIn" maxlength="20" placeholder="예: 1234" autocomplete="off">
      <div class="pk-hint" style="text-align:left">한글·영문·숫자 20자 이내 · 전화번호 같은 개인정보는 넣지 마세요</div>
      <button class="btn" style="width:100%;margin-top:10px" data-cu="login">확인</button>
      <button class="btn ghost" style="width:100%;margin-top:8px" data-cu="close">비회원으로 계속 (인기 상품 추천)</button>`}`;
  $("#custIn")?.focus();
}
function openCust(){renderCustSheet();$("#custOv").classList.add("on")}
$("#custBtn").addEventListener("click",openCust);
$("#todayHint").addEventListener("click",openCust);
$("#custOv").addEventListener("click",e=>{
  if(e.target.id==="custOv"){$("#custOv").classList.remove("on");return}
  const b=e.target.closest("[data-cu]");if(!b)return;const a=b.dataset.cu;
  if(a==="close") $("#custOv").classList.remove("on");
  if(a==="logout"){setCust(null);renderCustSheet()}
  if(a==="login"){const v=$("#custIn").value.trim();
    if(!CUST_RE.test(v)){toast("한글·영문·숫자 20자 이내로 입력해 주세요");return}
    setCust(v);$("#custOv").classList.remove("on");toast(`고객 ${v}님, 반가워요`)}
});
$("#custOv").addEventListener("keydown",e=>{if(e.key==="Enter"&&e.target.id==="custIn") $('[data-cu="login"]').click()});

/* ---------------- 추천 반응 기록: 추천을 눌러 담으면 click 기록, 주문할 때 rec_attrib 로 같이 보내 구매 여부 기록 */
let homeRec={rec_id:null,items:[]}, cartRec={rec_id:null,items:[]}, attrib={};
function noteRecClick(slot,id){
  const r=slot==="home"?homeRec:cartRec, i=r.items.findIndex(it=>it.id===id);if(i<0)return;
  const a={slot,algo:r.items[i].algo,rec_id:r.rec_id,rank:i+1};attrib[id]=a;
  API.event({event:"click",product_id:id,customer_id:cust,...a});
}

let homeSeq=0;
async function renderHome(){
  const seq=++homeSeq;
  try{const r=await API.home(cust,Object.keys(cart).filter(k=>cart[k]>0));
    if(seq!==homeSeq) return;
    homeRec={rec_id:r.rec_id,items:r.items};
    // 기존 고객: R "평소 자주 구매하시는 상품" / 신규·비회원: "인기 상품 추천"
    $("#todayTitle").textContent=(r.mode==="personal"?"🔁 ":"🔥 ")+(r.title||"오늘의 추천");
    $("#todayHint").hidden=!!cust||API!==Server;
    if(!r.items.length){$("#today").style.display="none";return}
    $("#todayList").innerHTML=r.items.map(it=>{const p=pmap()[it.id];return `<button class="hcard ${it.algo==="R"?"personal":""}" data-add="${it.id}" data-slot="home">
      <div class="emoji">${it.emoji}</div><div class="pname">${it.name}</div>
      <div class="meta"><span>${p?won(p.price):""}</span></div><small>${it.reason}</small></button>`}).join("");
    $("#today").style.display="block";
  }catch{$("#today").style.display="none"}
}

function renderCats(){
  $("#cats").innerHTML=CATS.map(c=>`<button class="cat ${c===cat?"on":""}" data-cat="${c}">${c}</button>`).join("");
}
$("#cats").addEventListener("click",e=>{const b=e.target.closest("[data-cat]");if(!b)return;cat=b.dataset.cat;renderGrid()});
const prodTags=p=>(p.robot===false?`<span class="tag staff">직원 준비</span>`:"")+(p.fragile?`<span class="tag fragile">깨짐주의</span>`:"");

function renderGrid(){
  renderCats();
  $("#grid").innerHTML=products.filter(p=>cat==="전체"||p.category===cat).map(p=>{
    const q=cart[p.id]||0, s=p.stock, cls=s===0?"zero":s<=LOW?"low":"";
    return `<div class="card ${q?"sel":""} ${s===0?"out":""}">
      <div class="emoji">${p.emoji}</div>
      <div class="pname">${p.name}</div>
      ${prodTags(p)?`<div class="ptags">${prodTags(p)}</div>`:""}
      <div class="meta"><span>${won(p.price)}</span><span class="stock ${cls}">${s===0?"품절":"남은 "+s+"개"}</span></div>
      <div class="qty"><button data-a="-" data-id="${p.id}" ${q?"":"disabled"} aria-label="빼기">−</button>
        <span>${q}</span><button data-a="+" data-id="${p.id}" ${q>=s?"disabled":""} aria-label="더하기">+</button></div>
    </div>`}).join("");
  const {ids,n,t}=cartSummary();
  $("#cnt").textContent=`담은 상품 ${n}개`;$("#total").textContent=won(t);$("#orderBtn").disabled=!n;$("#cartBtn").disabled=!n;
  if($("#cartOv").classList.contains("on")) renderCartSheet();
  const key=ids.slice().sort().join(",")+"|"+products.map(p=>p.stock).join(",");
  if(key!==recoKey){recoKey=key;updateReco(ids)}  // 장바구니(또는 재고)가 바뀔 때만 갱신
}

let recoSeq=0;
async function updateReco(ids){
  const seq=++recoSeq;  // 빠르게 여러 번 담을 때 늦게 온 예전 응답이 덮어쓰지 않게
  if(!ids.length){$("#reco").style.display="none";fitCart();return}
  try{const r=await API.recommend(ids,cust);
    if(seq!==recoSeq) return;
    cartRec={rec_id:r.rec_id,items:r.items};
    if(!r.items.length){$("#reco").style.display="none";fitCart();return}
    $("#recoTitle").textContent="💡 "+(r.title||"함께 구매하면 좋은 상품");
    $("#chips").innerHTML=r.items.map(it=>`<button class="chip ${it.level}" data-add="${it.id}" data-slot="cart">
      <span>${it.emoji} ${it.name} 추가</span><small>${it.reason}</small></button>`).join("");
    $("#reco").style.display="block";fitCart();
  }catch{}
}
// 아래 장바구니 바 높이에 맞춰 본문 여백·토스트 위치 조정
function fitCart(){document.documentElement.style.setProperty("--cartH",$("#cart").offsetHeight+"px")}
window.addEventListener("resize",fitCart);

/* ---------------- 장바구니 보기 */
function renderCartSheet(){
  const {m,ids,n,t}=cartSummary();
  $("#cartSheet").innerHTML=`<div class="sheet-h"><div class="st-title">🧺 장바구니 (${n}개)</div><button class="x" data-cart="close" aria-label="닫기">✕</button></div>
    ${ids.length?`<ul class="clist">${ids.map(k=>{const p=m[k],q=cart[k];return `<li>
      <span class="ce">${p.emoji}</span>
      <div class="cn"><b>${p.name}</b><small>${won(p.price)} × ${q} = ${won(p.price*q)}${p.robot===false?" · 직원 준비":""}</small></div>
      <div class="qty"><button data-cart="-" data-id="${k}" aria-label="빼기">−</button><span>${q}</span>
        <button data-cart="+" data-id="${k}" ${q>=p.stock?"disabled":""} aria-label="더하기">+</button></div>
      <button class="del" data-cart="del" data-id="${k}">삭제</button></li>`}).join("")}</ul>
      <div class="ctot"><button class="del" data-cart="clear">전체 비우기</button><span>합계 <b>${won(t)}</b></span></div>
      <button class="btn" style="width:100%" data-cart="order">로봇에게 주문하기</button>`
    :`<div class="empty" style="text-align:center;padding:28px 0">담은 상품이 없어요</div>
      <button class="btn ghost" style="width:100%" data-cart="close">상품 고르러 가기</button>`}`;
}
$("#cartBtn").addEventListener("click",()=>{renderCartSheet();$("#cartOv").classList.add("on")});
$("#cartOv").addEventListener("click",e=>{
  if(e.target.id==="cartOv"){$("#cartOv").classList.remove("on");return}  // 바깥 누르면 닫기
  const b=e.target.closest("[data-cart]");if(!b)return;const a=b.dataset.cart, id=b.dataset.id;
  if(a==="close"){$("#cartOv").classList.remove("on");return}
  if(a==="order"){$("#cartOv").classList.remove("on");$("#orderBtn").click();return}
  if(a==="+") cart[id]=Math.min((cart[id]||0)+1,pmap()[id].stock);
  if(a==="-") cart[id]=Math.max(0,(cart[id]||0)-1);
  if(a==="del") delete cart[id];
  if(a==="clear") cart={};
  for(const k in cart) if(!cart[k]) delete cart[k];
  renderGrid();  // 상품 목록·합계·추천 갱신 (+ 시트 다시 그림)
});

$("#grid").addEventListener("click",e=>{
  const b=e.target.closest("button[data-a]");if(!b)return;
  const id=b.dataset.id;cart[id]=Math.max(0,(cart[id]||0)+(b.dataset.a==="+"?1:-1));renderGrid();
});
function addOne(e){
  const b=e.target.closest("[data-add]");if(!b)return;const id=b.dataset.add, p=pmap()[id];if(!p)return;
  if((cart[id]||0)<p.stock){noteRecClick(b.dataset.slot,id);cart[id]=(cart[id]||0)+1;renderGrid();toast(`${p.name}을(를) 담았어요`)}
  else toast(`${p.name}은(는) 더 담을 수 없어요`);
}
$("#chips").addEventListener("click",addOne);
$("#todayList").addEventListener("click",addOne);

/* ---------------- 내 주문: order_id·client_secret 을 localStorage 에 저장 (새로고침해도 진행 상황·QR 유지) */
const MY_KEY="rokey_my_order", ACTIVE=["in_progress","ready_for_pickup"];
let my=null, pollGen=0, clock=0, countdown=null, seenStatus=null;  // clock: 서버 시각 - 내 시각(초)
function saveMy(){try{my?localStorage.setItem(MY_KEY,JSON.stringify(my)):localStorage.removeItem(MY_KEY)}catch{}}
function readMy(){try{const o=JSON.parse(localStorage.getItem(MY_KEY)||"null");return o&&o.order_id&&o.client_secret?o:null}catch{return null}}
function updateMyBtn(){$("#myPickup").hidden=!my}
function forgetMy(){my=null;saveMy();updateMyBtn()}

$("#orderBtn").addEventListener("click",async()=>{
  const items=Object.fromEntries(Object.entries(cart).filter(([,v])=>v>0));
  const ra=Object.fromEntries(Object.entries(attrib).filter(([k])=>items[k]));  // 추천을 눌러 담은 상품만
  try{const r=await API.order(items,cust,ra);
    attrib={};
    my={order_id:r.order_id,client_secret:r.client_secret};
    if(API===Server) saveMy();  // 브라우저 데모는 새로고침하면 사라지므로 저장 안 함
    updateMyBtn();openMy();
  }catch(e){toast(e.message)}
});
$("#myPickup").addEventListener("click",()=>my?openMy():toast("저장된 주문이 없어요"));

function openMy(){lastState=null;seenStatus=null;$("#ov").classList.add("on");startPolling()}
function closeMy(){$("#ov").classList.remove("on");pollGen++;clearTimeout(polling);clearInterval(countdown);loadProducts()}
function startPolling(){clearTimeout(polling);tick(++pollGen)}
async function tick(gen){
  if(gen!==pollGen||!my) return;
  let o;
  try{o=await API.myOrder(my.order_id,my.client_secret)}
  catch(e){
    if(gen!==pollGen) return;
    if(e.status===404||e.status===403){forgetMy();$("#ov").classList.remove("on");toast("주문 기록을 찾을 수 없어요");return}
    polling=setTimeout(()=>tick(gen),2000);return;  // 잠깐 연결이 끊긴 경우 다시 시도
  }
  if(gen!==pollGen) return;  // 그사이 닫았거나 다시 열었음
  clock=o.now-Date.now()/1000;
  const {now,...rest}=o, key=JSON.stringify(rest);
  if(key!==lastState){lastState=key;renderOrder(o)}
  // 수령(picked_up)·만료까지 계속 확인 (키오스크에서 받으면 이 화면도 바뀌게)
  if(ACTIVE.includes(o.status)) polling=setTimeout(()=>tick(gen),o.status==="in_progress"?700:2000);
}

const pad2=n=>String(n).padStart(2,"0");
const fmtLeft=sec=>{sec=Math.max(0,Math.round(sec));return `${Math.floor(sec/60)}:${pad2(sec%60)}`};
const hhmm=t=>{const d=new Date(t*1000);return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`};
const itemList=(title,obj)=>{const m=pmap();obj=obj||{};return Object.keys(obj).length?`<div class="plist-h">${title}</div><ul class="plist">${Object.entries(obj)
  .map(([k,v])=>`<li><span>${m[k]?m[k].emoji+" "+m[k].name:k}</span><b>${v}개</b></li>`).join("")}</ul>`:""};
const stepsHtml=(st)=>{const idx=st==="need_choice"?0:["checking","picking","done"].indexOf(st);
  return ["재고 확인","담는 중","완료"].map((t,i)=>
    `<div class="step ${i<idx||st==="done"?"done":i===idx?"on":""}"><div class="dot">${i<idx||st==="done"?"✓":i+1}</div>${t}</div>`).join("")};

function startCountdown(exp){
  clearInterval(countdown);
  const f=()=>{const e=$("#pkLeft");if(!e){clearInterval(countdown);return}
    const left=exp-(Date.now()/1000+clock);
    e.textContent=left>0?fmtLeft(left):"만료";e.classList.toggle("late",left<300)};
  f();countdown=setInterval(f,1000);
}

function renderOrder(o){
  const st=o.status, prev=seenStatus;seenStatus=st;
  clearInterval(countdown);
  if(st==="in_progress"){renderStatus(o.robot||{state:"checking",message:"주문을 로봇에게 보냈어요"});return}
  if(prev==="in_progress"){cart={};loadProducts()}  // 이번에 막 끝난 주문이면 장바구니 비우기
  const lists=itemList("🤖 로봇이 담은 상품 (바구니)",o.picked)+itemList("🧑‍💼 직원이 준비할 상품",o.staff_items);
  const closeBtn=t=>`<button class="btn" style="width:100%;margin-top:12px" id="closeOv">${t}</button>`, ok=closeBtn("확인");
  let body;
  if(st==="ready_for_pickup"){
    const qr=API.qrUrl(o.order_id,my.client_secret);
    body=`<div class="st-title">담기 완료! 픽업 준비됐어요</div>
      <div class="steps" style="margin-top:12px">${stepsHtml("done")}</div>
      <div class="pk-code"><small>픽업 번호 · 바구니 라벨</small><b>${o.pickup_code}</b></div>
      <div class="qrbox">${qr?`<img src="${qr}" alt="픽업 QR 코드" id="qrImg">`
        :`<div class="empty">브라우저 데모에서는<br>QR이 나오지 않아요</div>`}</div>
      <div class="st-msg" style="margin:4px 0 10px;color:var(--ink);font-weight:700">가게의 픽업 키오스크에 이 QR을 보여 주세요</div>
      <div class="pk-meta"><div>확인번호<b>${o.check}</b></div><div>만료까지<b id="pkLeft">-</b></div></div>
      <p class="pk-hint">QR이 안 되면 키오스크에 픽업 번호와 확인번호를 입력하세요</p>
      ${lists}${closeBtn("닫기")}`;
  }else if(st==="picked_up"){
    body=`<div class="robot">🎉</div><div class="st-title">수령 완료</div>
      <div class="st-msg">${o.pickup_code} 주문을 ${hhmm(o.picked_up_at)}에 받아 가셨어요. 감사합니다!</div>${lists}${ok}`;
  }else if(st==="expired"){
    body=`<div class="robot">⌛</div><div class="st-title">픽업 시간이 지났어요</div>
      <div class="st-msg">직원에게 픽업 번호 <b>${o.pickup_code}</b>를 알려 주세요</div>${lists}${ok}`;
  }else{  // cancelled / error
    body=`<div class="robot">${st==="error"?"⚠️":"🙅"}</div>
      <div class="st-title">${st==="error"?"문제가 생겼어요":"주문 취소"}</div>
      <div class="st-msg">${o.message||""}</div>${ok}`;
  }
  $("#sheet").innerHTML=body;
  const img=$("#qrImg");
  if(img) img.onerror=()=>{img.parentNode.innerHTML=`<div class="empty">QR을 불러오지 못했어요<br>확인번호를 대신 쓰세요</div>`};
  if(st==="ready_for_pickup") startCountdown(o.expires_at);
}

async function restoreMy(){  // 새로고침: 진행 중이거나 수령 대기 중인 주문이 있으면 다시 열기
  my=API===Server?readMy():null;updateMyBtn();if(!my) return;
  try{const o=await API.myOrder(my.order_id,my.client_secret);
    if(ACTIVE.includes(o.status)&&location.hash!=="#staff") openMy()}
  catch(e){if(e.status===404||e.status===403) forgetMy()}
}

/* 로봇 작업 중 화면 (재고 확인 / 손님 선택 / 담는 중) */
function renderStatus(s){
  const m=pmap(), st=s.state, stepHtml=stepsHtml(st);
  let body="";
  if(st==="need_choice"){
    const sh=s.shortage, p=m[sh.id], sub=s.substitute&&m[s.substitute];
    body=`<div class="robot">🤔</div><div class="st-title">${p.name} 재고가 부족해요</div>
      <div class="st-msg">${sh.want}개를 주문하셨는데 매대에 ${sh.have}개만 있어요</div>
      <div class="choices">
        ${sh.have>0?`<button class="btn" data-c="partial">${sh.have}개만 받을게요</button>`:""}
        ${sub&&sub.stock>0?`<button class="btn warn" data-c="substitute" data-s="${sub.id}">부족한 만큼 ${sub.emoji} ${sub.name}로 바꿀게요</button>`:""}
        <button class="btn ghost" data-c="cancel">주문 취소</button></div>`;
  }else{
    const pct=s.total?Math.round((s.done||0)/s.total*100):0, cur=s.current&&m[s.current];
    body=`<div class="robot busy">🤖</div>
      <div class="st-title">${st==="checking"?"재고 확인 중":cur?`${cur.emoji} ${cur.name} 담는 중`:"담는 중"}</div>
      <div class="st-msg">${s.message||""}</div>
      <div class="steps">${stepHtml}</div>
      <div class="prog"><i style="width:${pct}%"></i></div>
      <div class="meta" style="margin-top:6px"><span>진행</span><span>${s.done||0} / ${s.total||0}개</span></div>`;
  }
  $("#sheet").innerHTML=body;
}

$("#sheet").addEventListener("click",async e=>{
  const c=e.target.closest("[data-c]");
  if(c){c.disabled=true;await API.choice({choice:c.dataset.c,substitute:c.dataset.s||null});lastState=null;return}
  if(e.target.id==="closeOv") closeMy();
});

/* ---------------- 직원 화면 */
let staffTimer=null;
const STATE_TXT={ok:"정상",low:"보충 필요",order:"발주 필요",mismatch:"불일치"};
const attrTags=r=>(r.fragile?`<span class="tag attr fragile">깨짐주의</span>`:"")+(r.deformable?`<span class="tag attr soft">말랑</span>`:"")
  +(r.registered===false?`<span class="tag attr unreg">미등록</span>`:"");
const ROBOT_TXT={idle:"대기",checking:"재고 확인",picking:"담는 중",need_choice:"손님 선택 대기",done:"대기",cancelled:"대기",error:"오류"};
async function loadPickups(){
  let d;try{d=await API.pickups()}catch(e){return}
  $("#pk-sub").textContent=`준비 완료 후 ${d.expire_min}분 지나면 만료`;
  $("#pickups").innerHTML=d.orders.length?d.orders.map(o=>{const exp=o.status==="expired", left=o.expires_at-d.now;
    return `<tr><td><b>${o.pickup_code}</b></td><td>${hhmm(o.ready_at)}</td>
      <td class="n" style="text-align:left;${exp||left<300?"color:var(--danger)":""}">${exp?`${hhmm(o.expires_at)} 만료`:fmtLeft(left)}</td>
      <td class="n">${o.count}</td>
      <td>${exp?`<span class="tag mismatch">만료 · 바구니 정리</span>`:`<span class="tag ok">수령 대기</span>`}</td></tr>`}).join("")
    :`<tr><td colspan="5" class="empty">수령 대기 중인 주문이 없어요</td></tr>`;
}
async function loadStaff(){
  loadPickups();
  let d;try{d=await API.inventory()}catch(e){return}
  $("#inv").innerHTML=d.rows.map(r=>`<tr>
    <td>${r.emoji} ${r.name}${attrTags(r)}</td>
    <td>${r.robot?`<span class="tag real">실물</span>`:`<span class="tag staff">가상</span>`}</td><td class="n" style="${r.warehouse===0?"color:var(--danger)":""}">${r.warehouse}</td><td class="n">${r.stock}</td>
    <td class="n" style="${r.state==="mismatch"?"color:var(--danger)":""}">${r.vision??"-"}</td>
    <td><span class="tag ${r.state}">${STATE_TXT[r.state]}</span></td>
    <td><div class="mini"><button data-r="${r.id}" data-m="move" data-q="1" ${r.warehouse?"":"disabled"} title="창고에서 1개 꺼내 진열">진열 +1</button>
      <button data-r="${r.id}" data-m="receive" data-q="5" title="창고에 5개 입고">입고 +5</button>
      ${r.state==="mismatch"?`<button data-r="${r.id}" data-m="adjust" data-q="${r.vision-r.stock}">카메라값 반영</button>`:""}</div></td></tr>`).join("");
  $("#k-low").textContent=d.rows.filter(r=>r.state==="low"||r.state==="order").length;
  $("#k-mis").textContent=d.rows.filter(r=>r.state==="mismatch").length;
  $("#k-time").textContent=d.vision_time||"-";
  $("#k-robot").textContent=ROBOT_TXT[d.robot?.state]||"대기";
  $("#alerts").innerHTML=d.alerts.length?d.alerts.map(a=>`<li><span class="lv ${a.level}"></span><time>${a.time}</time><span>${a.text}</span></li>`).join("")
    :`<li class="empty">아직 알림이 없어요</li>`;
}
$("#inv").addEventListener("click",async e=>{
  const b=e.target.closest("button");if(!b)return;
  try{await API.restock(b.dataset.r,+b.dataset.q,b.dataset.m)}catch(e){toast(e.message)}loadStaff();loadProducts();
});
$("#scan").addEventListener("click",async()=>{await API.scan();toast("로봇이 매대를 점검하고 있어요");setTimeout(loadStaff,1800)});

/* ---------------- 탭 */
function show(v){
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.dataset.v===v));
  document.querySelectorAll(".view").forEach(x=>x.classList.toggle("on",x.id==="v-"+v));
  $("#cart").style.display=v==="shop"?"block":"none";fitCart();
  clearInterval(staffTimer);
  if(v==="staff"){loadStaff();staffTimer=setInterval(loadStaff,1500)}else loadProducts();
  history.replaceState(null,"",v==="staff"?"#staff":location.pathname);
}
document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>show(t.dataset.v)));

updateCustBtn();
loadProducts().then(()=>{show(location.hash==="#staff"?"staff":"shop");restoreMy()});
