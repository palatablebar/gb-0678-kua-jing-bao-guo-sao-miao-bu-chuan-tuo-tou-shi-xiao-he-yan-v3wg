from flask import render_template
from app.config import MODULES,TITLE,SHORT
from app.pipeline import execute,load_sample
from app import store
def register_pages(app):
    def page(index):
        run=store.latest() or execute(load_sample("dirty.csv"))
        stage=run["stages"][index]
        rows=stage["rows"]
        columns=[['scan_id', 'parcel', 'node', 'event_time', 'received_time'], ['scan_id', 'parcel', 'node', 'destination', 'zone'], ['scan_id', 'parcel', 'local_time', 'time_status_label', 'event_utc', 'received_utc', 'upload_lag_hours'], ['parcel', 'path_decision_label', 'accepted_at', 'delivered_at', 'provisional_delivered_at', 'scan_count', 'path'], ['parcel', 'sla', 'sla_binding', 'accepted_at', 'provisional_sla', 'working_hours', 'promised_hours', 'late_hours', 'expected'], ['parcel', 'expected', 'reported', 'difference', 'status'], ['parcel', 'delivered_at', 'sla', 'expected', 'status']][index]
        labels={'scan_id': '扫描号', 'parcel': '包裹号', 'node': '节点', 'event_time': '发生时间', 'received_time': '上传时间', 'destination': '目的地', 'zone': '时区', 'local_time': '当地时间', 'time_status_label': '时刻状态', 'event_utc': '发生 UTC', 'received_utc': '上传 UTC', 'upload_lag_hours': '补传时差 / h', 'path_decision_label': '路径裁决', 'accepted_at': '收件 UTC', 'delivered_at': '妥投 UTC', 'provisional_delivered_at': '临时妥投 UTC(最晚)', 'provisional_sla': '客服临时答复', 'delivery_uploaded_at': '妥投上传 UTC', 'scan_count': '扫描数', 'path': '节点路径', 'working_hours': '计时 / h', 'promised_hours': '承诺 / h', 'late_hours': '超时 / h', 'expected': '赔付 / 元', 'sla': '时效结论', 'sla_binding': '结论效力', 'reported': '申报 / 元', 'difference': '差额 / 元', 'status': '对账结论', 'accepted': '采纳扫描', 'late_uploads': '迟到上传', 'parcels': '包裹数', 'late_parcels': '正式超时', 'time_pending': '时效挂起', 'formal_parcels': '正式件', 'compensation': '赔付合计', 'delivered': '妥投数', 'path_errors': '路径异常', 'formal_paths': '正式路径', 'local_unique': '本地唯一时刻', 'dst_ambiguous': '回拨歧义', 'dst_skipped': '前跳空当', 'records': '归档数', 'formal_records': '正式归档', 'time_pending_sealed': '挂起封存', 'source_lines': '源行数', 'sha256': '摘要', 'ready': '可归档', 'differences': '差异数', 'settleable': '可结算'}
        for key,label in {'accepted_inputs': '采纳来源行', 'quarantine_inputs': '隔离来源行', 'output_records': '结果记录', 'input': '输入行', 'excluded': '排除行', 'balanced': '行数平衡', 'expected_total': '预期合计', 'reported_total': '申报合计', 'differences': '差异项', 'blank': '空行', 'repeated_headers': '分段表头', 'encoding': '编码', 'duplicate': '重复行', 'quarantine': '隔离行', 'accepted': '采纳行', 'sha256': '校验摘要', 'ready': '可归档'}.items(): labels.setdefault(key,label)
        pending=[r for r in run["stages"][3]["rows"] if r.get("path_decision")=="TIME_PENDING"]
        return render_template(f"m{index+1}.html",title=TITLE,short=SHORT,modules=MODULES,index=index,run=run,stage=stage,rows=rows,columns=columns[:12],history=store.history(),labels=labels,pending=pending)
    app.add_url_rule("/",view_func=lambda:page(4))
    for index,(slug,name) in enumerate(MODULES):
        app.add_url_rule("/"+slug,endpoint="page_"+slug,view_func=lambda index=index:page(index))
