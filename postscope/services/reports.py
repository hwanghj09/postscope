import csv
import io
import json
import threading
import zipfile
import base64
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from jinja2 import Environment, FileSystemLoader, select_autoescape
from plotly.offline import get_plotlyjs
from ..storage import now,uid

ROOT=Path(__file__).resolve().parents[1]
PLOT_LOCK=threading.Lock()


def public_snapshot(result, include_ids=False):
    data=json.loads(json.dumps(result))
    if not include_ids:
        data["config"]["sender"]=data.get("sender_label","선택한 가명 계정·범주")
        data["config"]["chat_key"]=data["chat_title"]
        data["config"].pop("workspace_id",None)
        for window in data["quality"]["windows"]:
            window.pop("chat_key",None)
            window.pop("workspace_id",None)
    data["include_ids"]=include_ids
    data.pop("sessions",None)
    return data


def csv_bytes(rows):
    buffer=io.StringIO(newline="")
    writer=csv.writer(buffer)
    for row in rows:
        writer.writerow([("'"+value if value.lstrip().startswith(("=","+","-","@","\t","\r")) else value) if isinstance(value,str) else value for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def png_charts(result):
    font_path=ROOT/"assets"/"NanumGothic-Regular.ttf"
    font=FontProperties(fname=font_path) if font_path.exists() else FontProperties()
    images={}
    with PLOT_LOCK:
        for name in ("daily","weekday","hourly","heatmap"):
            fig,ax=plt.subplots(figsize=(16,6),dpi=110)
            fig.set_facecolor("white")
            if name=="daily":
                days=result["daily"]
                values=[float('nan') if d['status']=='unknown' and d['count']==0 else d['count'] for d in days]
                ax.plot(range(len(days)),values,color="#3162e8",linewidth=2)
                ax.fill_between(range(len(days)),values,alpha=.08,color="#3162e8")
                indices=list(range(0,len(days),max(1,len(days)//8)))
                ax.set_xticks(indices,[days[i]["date"] for i in indices])
                title="날짜별 관측 게시량"
            elif name=="weekday":
                ax.bar(["월","화","수","목","금","토","일"],result["weekday"],color="#3162e8",width=.55)
                title="요일별 게시 분포"
            elif name=="hourly":
                ax.bar(range(24),result["hours"],color="#3162e8",width=.7)
                title="시간대별 게시 분포"
            else:
                image=ax.imshow(result["heatmap"],cmap="Blues",aspect="auto")
                ax.set_xticks(range(24))
                ax.set_yticks(range(7),["월","화","수","목","금","토","일"])
                fig.colorbar(image,ax=ax)
                title="요일 × 시간 관측 게시량"
            ax.set_title(title+" · "+result["config"]["timezone"],fontproperties=font,pad=22,fontsize=18)
            for text in ax.get_xticklabels()+ax.get_yticklabels():
                text.set_fontproperties(font)
            ax.spines[["top","right"]].set_visible(False)
            ax.grid(axis="y",alpha=.1)
            fig.text(.02,.015,"PostScope · 생성 시점 스냅샷 · "+result["quality"]["coverage"],fontproperties=font,color="#64748b",fontsize=10)
            fig.tight_layout(rect=(0,.04,1,1))
            output=io.BytesIO()
            fig.savefig(output,format="png")
            plt.close(fig)
            images[name]=output.getvalue()
    return images


def create_report(store,analysis_id,format,include_ids=False):
    with store.connect() as db:
        row=db.execute("SELECT workspace_id,result FROM analysis_runs WHERE id=?",(analysis_id,)).fetchone()
    if not row:
        raise ValueError("분석 스냅샷을 찾을 수 없습니다.")
    result=public_snapshot(json.loads(row["result"]),include_ids)
    report_id=uid()
    if format=="html":
        env=Environment(loader=FileSystemLoader(ROOT/"templates"),autoescape=select_autoescape())
        data=json.dumps(result,ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
        font=base64.b64encode((ROOT/'assets'/'NanumGothic-Regular.ttf').read_bytes()).decode()
        content=env.get_template("report.html").render(result=result,plotly=get_plotlyjs(),data=data,font=font).encode()
        extension="html"
    elif format=="png":
        content=png_charts(result)["heatmap"]
        extension="png"
    elif format in {"csv","zip"}:
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("summary.csv",csv_bytes([["지표","값","계산 가능 여부"],*[[k,json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v,v is not None] for k,v in result["metrics"].items()]]))
            archive.writestr("daily.csv",csv_bytes([["날짜 ("+result["config"]["timezone"]+")","관측 건수","관측 상태","관측 시간","7일 이동평균"],*[[d["date"],d["count"],d["status"],d["hours"],d["moving_average"]] for d in result["daily"]]]))
            archive.writestr("weekday_hour.csv",csv_bytes([["요일","시각 ("+result["config"]["timezone"]+")","건수","관측 시간","시간당 게시율","확인된 구간 내 건수 (게시율 분자)"],*[[["월","화","수","목","금","토","일"][w],h,result["heatmap"][w][h],result["exposure"][w][h],result["rates"][w][h],result.get('normalized_counts',result['heatmap'])[w][h]] for w in range(7) for h in range(24)]]))
            archive.writestr("intervals.csv",csv_bytes([["관측 게시 간격 (초)","계산 포함","제외 사유"],*[[r["seconds"],r["included"],r["reason"]] for r in result["intervals"]]]))
            archive.writestr("analysis_config.json",json.dumps({k:result[k] for k in ("config","quality","algorithm","snapshot_id","created_at","comparison","incomplete_albums","include_ids")},ensure_ascii=False,indent=2))
            archive.writestr("README.txt","PostScope 생성 시점 집계 스냅샷입니다. 관측되지 않은 구간은 게시 0건을 의미하지 않습니다. CSV 비율은 반올림 전 원값입니다. 서명은 개인 식별 정보가 아닙니다. 메시지 본문·로그인 비밀값·가명 매핑은 포함하지 않습니다.")
            if format=="zip":
                for name,image in png_charts(result).items():
                    archive.writestr("charts/"+name+".png",image)
        content=buffer.getvalue()
        extension="zip"
    else:
        raise ValueError("지원하지 않는 보고서 형식입니다.")
    filename=report_id+"."+extension
    path=store.directory/"reports"/filename
    try:
        path.write_bytes(content)
        with store.connect() as db:
            db.execute("INSERT INTO report_snapshots VALUES(?,?,?,?,?,?)",(report_id,row["workspace_id"],analysis_id,format,now(),filename))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"id":report_id,"filename":"PostScope-"+result["config"]["start"]+"."+extension,"download_url":"/api/reports/"+report_id+"/download"}
