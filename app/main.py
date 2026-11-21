from flask import Flask,jsonify,request,send_from_directory
from app.config import ROOT,DATA_DIR,MODULES
from app.errors import DomainError
from app import pipeline,store
from app.web import register_pages
import importlib
def create_app():
    application=Flask(__name__,template_folder="templates",static_folder="static")
    application.config["MAX_CONTENT_LENGTH"]=4*1024*1024
    @application.errorhandler(DomainError)
    def domain_error(error): return jsonify({"code":error.code,"message":error.message}),error.status
    @application.errorhandler(KeyError)
    @application.errorhandler(TypeError)
    @application.errorhandler(ValueError)
    def invalid_shape(error): return jsonify({"code":"INVALID_INPUT","message":"字段或参数无效，请核对输入"}),422
    @application.errorhandler(413)
    def too_large(error): return jsonify({"code":"TOO_LARGE","message":"文件超过4MB"}),413
    @application.get("/healthz")
    def health(): return {"status":"ok"}
    @application.post("/api/runs")
    def runs():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
        params=payload.get("parameters",{})
        if not isinstance(params,dict): raise DomainError("BAD_PARAMETERS","参数需要对象")
        raw=payload.get("csv")
        if raw is not None and not isinstance(raw,str): raise DomainError("BAD_CSV","CSV需要文本")
        return pipeline.execute(raw.encode() if raw is not None else pipeline.load_sample(payload.get("sample","dirty.csv")),params)
    @application.post("/api/upload")
    def upload():
        file=request.files.get("file")
        if not file: raise DomainError("NO_FILE","请选择CSV文件")
        return pipeline.execute(file.read())
    @application.get("/api/runs")
    def history(): return {"runs":store.history()}
    @application.get("/api/runs/<run_id>")
    def run_detail(run_id): return store.get(run_id)
    @application.post("/api/runs/<run_id>/review")
    def review(run_id):
        p=request.get_json(silent=True) or {}
        if not isinstance(p.get("version"),int): raise DomainError("BAD_VERSION","请提供版本号")
        return store.review(run_id,p["version"],p.get("decision"))
    @application.get("/api/runs/<run_id>/archive")
    def archive(run_id):
        from app.archive import build_archive
        run=store.get(run_id)
        return application.response_class(build_archive(run),mimetype="text/csv",headers={"Content-Disposition":'attachment; filename="evidence.csv"'})
    @application.post("/api/runs/<run_id>/claims")
    def claim(run_id):
        # 幂等赔付：同一批次同一包裹重试只生效一次，重复请求返回 already_claimed。
        run=store.get(run_id)
        p=request.get_json(silent=True) or {}
        parcel=p.get("parcel")
        if not parcel: raise DomainError("BAD_PARCEL","请提供包裹号")
        settleable={str(r.get("parcel")):r for r in run["stages"][5]["rows"] if r.get("settleable")}
        if str(parcel) not in settleable: raise DomainError("NOT_SETTLEABLE","时效挂起件不可结算赔付",409)
        amount=settleable[str(parcel)].get("expected","0")
        created=store.claim_compensation(run_id,parcel,amount)
        return jsonify({"parcel":str(parcel),"amount":amount,"claimed":created,"already_claimed":not created,"claims":store.list_claims(run_id)})
    @application.get("/api/runs/<run_id>/raw")
    def raw_scans(run_id):
        # 原始扫描只读视图：任何重算/替换都不改写，用于核对与误删防护。
        run=store.get(run_id)
        stage=run["stages"][0]
        return jsonify({"run_id":run_id,"input_sha256":run["manifest"]["input_sha256"],
                        "accepted_scans":stage["rows"],
                        "intake_quarantine":stage["quarantine"],
                        "metrics":stage["metrics"]})
    for index,(slug,title) in enumerate(MODULES,1):
        module=importlib.import_module(f"app.modules.m{index}_{slug}.handler")
        application.register_blueprint(module.bp)
    register_pages(application)
    return application
app=create_app()
if __name__=="__main__": app.run(host="0.0.0.0",port=8000,threaded=True)
