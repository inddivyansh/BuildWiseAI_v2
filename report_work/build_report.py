from pathlib import Path
from copy import deepcopy
import hashlib
import shutil
import zipfile
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "2M_Intern_Report.docx"
OUT = ROOT / "INTERNSHIP_REPORT_COMPLETED.docx"
ASSETS = ROOT / "report_work" / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

def set_font(run, size=12, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:ascii"), "Times New Roman")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Times New Roman")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor(0, 0, 0)

def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)

def set_cell_margins(cell, top=90, start=100, bottom=90, end=100):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in("w:tcMar")
    if tcMar is None:
        tcMar = OxmlElement('w:tcMar')
        tcPr.append(tcMar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tcMar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tcMar.append(node)
        node.set(qn("w:w"), str(v)); node.set(qn("w:type"), "dxa")

def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement('w:tblHeader'); el.set(qn('w:val'), 'true'); trPr.append(el)

def add_page_field(paragraph):
    run = paragraph.add_run()
    fldChar1 = OxmlElement('w:fldChar'); fldChar1.set(qn('w:fldCharType'), 'begin')
    instrText = OxmlElement('w:instrText'); instrText.set(qn('xml:space'), 'preserve'); instrText.text = ' PAGE '
    fldChar2 = OxmlElement('w:fldChar'); fldChar2.set(qn('w:fldCharType'), 'end')
    run._r.append(fldChar1); run._r.append(instrText); run._r.append(fldChar2)
    set_font(run, 10)

def set_update_fields(doc):
    settings = doc.settings.element
    update = settings.find(qn('w:updateFields'))
    if update is None:
        update = OxmlElement('w:updateFields'); settings.append(update)
    update.set(qn('w:val'), 'true')

def clear_body(doc):
    body = doc._element.body
    sectPr = body.sectPr
    for child in list(body):
        if child is not sectPr:
            body.remove(child)

def setup_styles(doc):
    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'; normal._element.rPr.rFonts.set(qn('w:ascii'), 'Times New Roman'); normal._element.rPr.rFonts.set(qn('w:hAnsi'), 'Times New Roman')
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.space_after = Pt(6)
    for name, size, align in [('Heading 1',17,WD_ALIGN_PARAGRAPH.CENTER),('Heading 2',14,WD_ALIGN_PARAGRAPH.LEFT),('Heading 3',12,WD_ALIGN_PARAGRAPH.LEFT)]:
        s=doc.styles[name]; s.font.name='Times New Roman'; s._element.rPr.rFonts.set(qn('w:ascii'),'Times New Roman'); s._element.rPr.rFonts.set(qn('w:hAnsi'),'Times New Roman'); s.font.size=Pt(size); s.font.bold=True; s.font.color.rgb=RGBColor(0,0,0); s.paragraph_format.alignment=align; s.paragraph_format.space_before=Pt(12); s.paragraph_format.space_after=Pt(6); s.paragraph_format.line_spacing=1.15
    if 'Caption' in doc.styles:
        s=doc.styles['Caption']; s.font.name='Times New Roman'; s.font.size=Pt(11); s.font.italic=False; s.font.color.rgb=RGBColor(0,0,0); s.paragraph_format.alignment=WD_ALIGN_PARAGRAPH.CENTER

def para(doc, text='', style=None, align=WD_ALIGN_PARAGRAPH.JUSTIFY, bold_lead=None):
    p=doc.add_paragraph(style=style)
    p.alignment=align
    p.paragraph_format.line_spacing=1.15
    p.paragraph_format.space_after=Pt(6)
    if bold_lead and text.startswith(bold_lead):
        r=p.add_run(bold_lead); set_font(r,12,bold=True)
        r=p.add_run(text[len(bold_lead):]); set_font(r,12)
    else:
        r=p.add_run(text); set_font(r,12)
    return p

def heading(doc, text, level=1):
    return para(doc, text, style=f'Heading {level}', align=WD_ALIGN_PARAGRAPH.CENTER if level==1 else WD_ALIGN_PARAGRAPH.LEFT)

def page(doc):
    doc.add_page_break()

def table(doc, headers, rows, widths=None):
    t=doc.add_table(rows=1, cols=len(headers)); t.alignment=WD_TABLE_ALIGNMENT.CENTER
    hdr=t.rows[0]; set_repeat_table_header(hdr)
    for i,h in enumerate(headers):
        c=hdr.cells[i]; c.text=''; shade(c,'1F4E78'); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; set_cell_margins(c)
        p=c.paragraphs[0]; p.alignment=WD_ALIGN_PARAGRAPH.CENTER; r=p.add_run(h); set_font(r,10,bold=True); r.font.color.rgb=RGBColor(255,255,255)
    for ri,row in enumerate(rows):
        cells=t.add_row().cells
        for i,val in enumerate(row):
            c=cells[i]; c.text=''; c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER; set_cell_margins(c)
            if ri%2: shade(c,'EAF2F8')
            p=c.paragraphs[0]; p.alignment=WD_ALIGN_PARAGRAPH.LEFT; r=p.add_run(str(val)); set_font(r,9)
    if widths:
        for row in t.rows:
            for cell,w in zip(row.cells,widths): cell.width=Inches(w)
    doc.add_paragraph()
    return t

def caption(doc, text):
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_after=Pt(8); r=p.add_run(text); set_font(r,11); return p

def font(size, bold=False):
    try: return ImageFont.truetype('C:/Windows/Fonts/times.ttf', size)
    except: return ImageFont.load_default()

def box(draw, xy, title, lines, color):
    x1,y1,x2,y2=xy; draw.rounded_rectangle(xy, radius=16, fill=color, outline=(31,78,121), width=3)
    draw.text((x1+14,y1+12), title, fill='white', font=font(22,True))
    y=y1+52
    for line in lines:
        draw.text((x1+14,y), line, fill='white', font=font(16)); y+=24

def arrow(draw, p1,p2):
    draw.line([p1,p2], fill=(31,78,121), width=4)
    x,y=p2; draw.polygon([(x,y),(x-12,y-7),(x-12,y+7)], fill=(31,78,121))

def create_architecture():
    path=ASSETS/'architecture.png'; im=Image.new('RGB',(1600,1060),'white'); d=ImageDraw.Draw(im)
    d.text((455,15),'BuildWise v2 verified component architecture',fill=(0,0,0),font=font(30,True))
    box(d,(70,110,450,270),'Web client',['Next.js / TypeScript','Project dashboard','2D Konva / 3D Three.js','PDF evidence viewer'],(31,78,121))
    box(d,(610,110,990,270),'Unified API',['FastAPI application','JWT / local development auth','Pydantic request models','SSE run events'],(0,112,144))
    box(d,(1150,110,1530,270),'Persistence',['Neo4j graph repository','Project-scoped storage','In-process LRU plan cache','Uploaded artefacts'],(76,114,176))
    box(d,(210,480,640,690),'Generation and editing',['Prompt or structured specification','Rule-based / optional LLM parsing','Constraint solver and cleanup','DXF / SVG export'],(85,107,47))
    box(d,(960,480,1390,690),'Compliance verification',['PDF/DXF/raster ingestion','Asset and clause extraction','Constraint extraction / retrieval','Deterministic checker'],(154,95,17))
    box(d,(560,820,1040,980),'Auditable result',['Physical assets, regulations, and verdict edges','COMPLIES_WITH / VIOLATES / NEEDS_REVIEW','Frontend overlays and evidence inspection'],(112,48,160))
    arrow(d,(450,190),(610,190)); arrow(d,(990,190),(1150,190)); arrow(d,(800,270),(425,480)); arrow(d,(800,270),(1175,480)); arrow(d,(425,690),(750,820)); arrow(d,(1175,690),(850,820)); arrow(d,(800,690),(800,820))
    im.save(path); return path

def add_figure(doc,path,title,width=6.2):
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.add_run().add_picture(str(path),width=Inches(width)); caption(doc,title)

def title_page(doc):
    p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_before=Pt(45)
    r=p.add_run('BUILDWISE V2'); set_font(r,20,True)
    logo = ASSETS / 'iiitp_logo.jpeg'
    if not logo.exists():
        with zipfile.ZipFile(TEMPLATE) as archive:
            media = [n for n in archive.namelist() if n.startswith('word/media/')]
            if media:
                logo.write_bytes(archive.read(media[0]))
    if logo.exists():
        p=doc.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(str(logo),width=Inches(1.15))
    para(doc,'An Integrated Platform for Floor Plan Generation and Deterministic Building Code Compliance Verification',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'A Project Report submitted in partial fulfillment of the requirements for the award of the degree of',align=WD_ALIGN_PARAGRAPH.CENTER)
    p=para(doc,'Bachelor of Technology',align=WD_ALIGN_PARAGRAPH.CENTER); p.runs[0].font.size=Pt(16); p.runs[0].font.bold=True
    para(doc,'in',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'Computer Science and Engineering',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'by',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'[STUDENT NAME] (MIS: [MIS NUMBER])',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'External Supervisor: [ORGANIZATION SUPERVISOR NAME AND DESIGNATION]',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'Internship Organization: [ORGANIZATION NAME]',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'Semester: [SEMESTER]     Internship Period: [START DATE] to [END DATE]',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'Department of Computer Science and Engineering',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'Indian Institute of Information Technology Pune',align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc,'[MONTH YEAR]',align=WD_ALIGN_PARAGRAPH.CENTER)
    page(doc)

def front_matter(doc):
    heading(doc,'BONAFIDE CERTIFICATE',1)
    para(doc,'This is to certify that the internship project report entitled “BuildWise v2: An Integrated Platform for Floor Plan Generation and Deterministic Building Code Compliance Verification” submitted by [STUDENT NAME], bearing MIS No. [MIS NUMBER], is submitted in partial fulfillment of the requirements for the award of the degree of Bachelor of Technology in the Department of Computer Science and Engineering, Indian Institute of Information Technology Pune. The internship organization, supervisor, dates, and all signatures are subject to official confirmation.')
    para(doc,'Internal Supervisor: [NAME AND SIGNATURE]                              External Supervisor: [NAME, DESIGNATION, AND SIGNATURE]',align=WD_ALIGN_PARAGRAPH.LEFT)
    para(doc,'Head of Department: [NAME AND SIGNATURE]                              Date: [DATE]',align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)
    heading(doc,'UNDERTAKING FOR PLAGIARISM',1)
    para(doc,'I, [STUDENT NAME], solemnly declare that the work presented in this report is my internship project work. Contributions and sources used for background information are acknowledged in the references. I understand the Indian Institute of Information Technology Pune policy on plagiarism and will submit the final report only after the required verification and approvals.')
    para(doc,'Student signature: ____________________     Date: ____________________',align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)
    heading(doc,'CONFLICT OF INTEREST',1)
    para(doc,'Project title: BuildWise v2: An Integrated Platform for Floor Plan Generation and Deterministic Building Code Compliance Verification.')
    para(doc,'The author declares that any organizational affiliations, financial interests, or non-financial interests relevant to this internship must be disclosed here before final submission. Based on the repository alone, no such information can be verified. [CONFIRM OR REPLACE THIS DECLARATION AS REQUIRED BY THE INSTITUTE.]')
    para(doc,'Student signature: ____________________     Date: ____________________',align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)
    heading(doc,'ACKNOWLEDGEMENT',1)
    para(doc,'I express my sincere gratitude to [ORGANIZATION NAME] for providing the internship opportunity and to [EXTERNAL SUPERVISOR NAME] for guidance during the development and review of this work. I thank the faculty members of the Department of Computer Science and Engineering, Indian Institute of Information Technology Pune, especially [INTERNAL SUPERVISOR NAME], for their academic support and feedback. I also acknowledge peers and reviewers whose discussions helped improve the clarity and reliability of the implementation. This acknowledgement is a draft and should be personalized before submission.')
    page(doc)
    heading(doc,'ABSTRACT',1)
    para(doc,'BuildWise v2 is a web-based Architecture, Engineering, and Construction (AEC) software project that links early-stage floor-plan creation with evidence-oriented building-code verification. The repository implements a unified FastAPI backend and a Next.js frontend. A user can create a project, generate a structured floor plan from a natural-language brief or structured specification, edit the design, inspect it in 2D and 3D, and export supported drawing formats. A second workflow accepts project documents and codebooks, extracts sheets, assets, clauses, and constraints, then evaluates applicable constraints using deterministic program logic. The system records project-scoped entities and audit relationships in Neo4j so that an interface can associate a visual asset with a regulation and an audit verdict. Optional large-language-model and remote GPU integrations are deliberately separated from the deterministic decision path; their availability depends on configured providers, credentials, and model assets. The implementation is therefore best understood as an integrated prototype platform rather than a certified plan-approval system. This report documents the architecture, source-level implementation, verified repository evidence, limitations, and realistic next steps.')
    para(doc,'Keywords: architecture engineering and construction, floor-plan generation, building-code compliance, FastAPI, Next.js, Neo4j, computational geometry, BIM',bold_lead='Keywords:')
    page(doc)

def toc(doc):
    heading(doc,'TABLE OF CONTENTS',1)
    para(doc,'The heading structure below is configured for automatic field update when the document is opened in Microsoft Word. Update the Table of Contents field before printing to refresh page numbers.',align=WD_ALIGN_PARAGRAPH.LEFT)
    for item in ['Abstract','List of Figures','List of Tables','Chapter 1  Introduction','  1.1 Overview of Work','  1.2 Literature Review and Technical Context','  1.3 Motivation and Scope','  1.4 Objectives and Research Gap','Chapter 2  Problem Statement','  2.1 Requirements and Objectives','  2.2 System Analysis and Design','Chapter 3  Proposed Work','  3.1 Architecture and Data Flow','  3.2 Implementation Details','  3.3 Technology and Hardware Software Requirements','Chapter 4  Results and Discussion','Chapter 5  Conclusion and Future Scope','References','Internship Completion Certificate']:
        para(doc,item,align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)
    heading(doc,'LIST OF FIGURES',1)
    for x in ['Figure 1. BuildWise v2 verified component architecture','Figure 2. Project compliance workspace repository screenshot','Figure 3. Example detail selection repository screenshot']:
        para(doc,x,align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)
    heading(doc,'LIST OF TABLES',1)
    for x in ['Table 1. Technology stack evidenced in the repository','Table 2. Representative API request and response flows','Table 3. Graph entities and principal relationships','Table 4. Feature verification status','Table 5. Repository evidence and limitations']:
        para(doc,x,align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)

def chapters(doc):
    heading(doc,'CHAPTER 1',1); heading(doc,'INTRODUCTION',1)
    heading(doc,'1.1 Overview of Work',2)
    para(doc,'BuildWise v2 addresses the disconnect between schematic building-design work and the later, evidence-intensive task of checking drawings against regulatory requirements. In the repository, the product is implemented as a unified application: the web client provides project, editor, and compliance-workspace pages; the FastAPI service exposes generation, ingestion, export, project, document, and verification endpoints; and Neo4j stores project-scoped graph entities. The central design principle visible in the verification modules is that a language model may assist with extraction or interpretation, but the final compliance check is made by deterministic functions that compare grounded measurements with explicit constraints.')
    para(doc,'The intended users are architects and design consultants creating preliminary layouts, contractors reviewing design submissions, and project or compliance reviewers who need a traceable view of a document asset, a governing clause, and a verdict. The implementation supports two related paths. The Blueprint Studio path transforms a brief or specification into a FloorPlanSpec and then an editable GeneratedFloorPlan. The PlanLint path uploads building drawings and regulatory documents, creates a sheet and asset representation, retrieves or extracts constraints, and records verdicts for the project.')
    heading(doc,'1.2 Literature Review and Technical Context',2)
    para(doc,'The technical choices are consistent with established web and AEC software practices. Next.js App Router uses file-system routing and layouts for React web applications [1], which corresponds to the repository’s app-directory pages for dashboards, projects, editors, login, and signup. FastAPI provides OpenAPI-based endpoint descriptions and interactive API documentation [2], matching the FastAPI route and Pydantic schema organization in the backend. Neo4j’s property-graph model and declarative Cypher query language are appropriate for representing linked assets, regulations, constraints, and audit outcomes [3].')
    para(doc,'For interchange, Industry Foundation Classes (IFC) is an open, vendor-neutral standard for digital descriptions of built assets [4]. The repository contains an IFC builder module and IFC ingestion support, while the currently mounted export endpoint exposes only DXF and SVG. This distinction matters: the source demonstrates an IFC-oriented implementation component, but it does not by itself verify a complete, endpoint-accessible IFC export workflow. In the same way, document extraction and LLM tooling are included as optional capabilities, rather than evidence that a model has been trained or evaluated on a particular dataset.')
    heading(doc,'1.3 Motivation and Scope',2)
    para(doc,'Manual drawing review is difficult because a reviewer must relate graphical geometry, dimensional annotations, schedules, codebook text, and exceptions that may appear on different sheets. BuildWise v2 scopes the problem to a prototype platform that can make those relationships explicit. The system does not replace professional judgment, a licensed architect, or a regulatory authority. It is not verified as a certified code-approval product, and the repository contains no evidence of deployment, production-scale throughput, or jurisdiction-wide rule completeness.')
    heading(doc,'1.4 Objectives and Research Gap',2)
    para(doc,'The implementation objectives are to (i) represent floor-plan requirements with typed data models; (ii) generate, edit, render, and export editable plans; (iii) ingest selected architectural and regulatory documents; (iv) extract assets and constraints; (v) execute deterministic checks; and (vi) expose auditable project results in the interface. The gap addressed is not a claim of universal automated compliance; it is an engineering integration gap between generation tools, geometry operations, document ingestion, and evidence-linked checking. The repository does not establish empirical accuracy for vision extraction, LLM parsing, or compliance coverage across building codes, so these remain validation targets rather than achieved research metrics.')
    page(doc)
    heading(doc,'CHAPTER 2',1); heading(doc,'PROBLEM STATEMENT',1)
    heading(doc,'2.1 Requirements and Objectives',2)
    para(doc,'The practical problem is to provide a single project workspace in which a user can form a preliminary floor plan, preserve it with project context, submit drawings and codebooks, and inspect deterministic compliance results. Existing standalone drawing tools typically do not retain an explicit chain between a measured asset and a regulatory clause. Conversely, document review tools may not provide a convenient path from a generated plan to an auditable input. BuildWise v2 is designed to provide that bridge while keeping the geometry-based verdict function separate from probabilistic extraction components.')
    table(doc,['Requirement class','Repository-supported requirement'],[
        ['Functional','Create, retrieve, update, and delete graph-backed projects; upload documents; initiate verification; list audit results and runs.'],
        ['Functional','Generate a plan from text or a typed FloorPlanSpec; edit through structured actions or optional LLM parsing; render in 2D/3D.'],
        ['Functional','Ingest supported floor-plan files, export supported formats, and send client-side vector PDF output to a compliance project.'],
        ['Functional','Extract document sheets/assets/clauses/constraints; run deterministic comparisons; return verdicts and evidence metadata.'],
        ['Non-functional','Use Pydantic validation, CORS configuration, project-scoped repository queries, JWT validation when enabled, and local-development fallback.'],
        ['Boundary','Do not claim statutory certification, complete code coverage, model accuracy, or production availability without additional validation.'],
    ],[1.4,5.0]); caption(doc,'Table 1. Requirements derived from the source implementation')
    heading(doc,'2.2 System Analysis and Design',2)
    para(doc,'The application entry point is planlint/api/main.py:create_app. Its lifespan creates a Neo4j async driver and GraphRepository in normal operation, initializes the schema, creates an Embedder, and initializes the RunManager. It mounts PlanLint routers at both unprefixed and /api paths, then mounts blueprint-history and generator-related routers under /api. A legacy standalone app module remains for isolated generation operation but is explicitly not the unified production entry point.')
    para(doc,'The frontend follows the Next.js App Router pattern. Page modules provide the dashboard, project list, project compliance workspace, editor, login, and signup views. Client-side state uses Zustand stores for model and floor-plan state, React Query for request workflows, Konva for the 2D plan canvas, and React Three Fiber/Three.js for the 3D view. PlanViewer renders a PDF page through PDF.js and draws asset overlays that respond to verdict and selection state.')
    add_figure(doc,create_architecture(),'Figure 1. BuildWise v2 verified component architecture')
    page(doc)
    heading(doc,'CHAPTER 3',1); heading(doc,'PROPOSED WORK',1)
    heading(doc,'3.1 Architecture and Data Flow',2)
    para(doc,'In the generation path, GenerateDialog or NLInput creates a request to POST /api/generate. The route validates provider selection only when an LLM mode is requested, calls GenerationPipeline.generate_from_nl, places a successful plan in an in-process LRU PlanStore, and synchronously persists it through the graph repository when project_id is supplied. The response contains a generation identifier, plan data, parsed specification, optional compliance result, and persistence context. The editor route obtains the current plan from the session store or durable blueprint record, applies structured edits through app/services/edit/editor.py, and persists an edited version when the source was project-backed.')
    para(doc,'In the compliance path, a user creates a project and uploads a floor plan or codebook. The route saves document metadata and starts a verification run. The pipeline processes pending documents, derives sheets and physical assets, derives or retrieves regulations and constraints, applies planlint/verify/checker.py to compatible asset-constraint pairs, writes a closed-enum verdict relationship, and emits run events for the SSE endpoint. Project results combine documents, sheets, assets, clauses, reference links, specifications, details, and verdict data for the dual-pane interface.')
    table(doc,['API endpoint','Purpose and main response evidence'],[
        ['POST /api/generate','Accepts GenerateRequest; returns GenerateResponse with generated plan and optional persistence context.'],
        ['POST /api/edit','Accepts structured edit actions or prompt; returns edit status, revised generation ID, plan, and changes.'],
        ['POST /api/ingest','Accepts an uploaded file and optional project ID; converts supported plan input into an editable plan representation.'],
        ['GET /api/export/{generation_id}/{fmt}','Provides a stored plan in mounted export formats, currently DXF or SVG at this route.'],
        ['POST /projects/{id}/documents','Stores a project-scoped floor-plan or codebook document and returns document metadata.'],
        ['POST /projects/{id}/verify','Creates an asynchronous verification run; GET /runs/{run_id}/events streams progress.'],
        ['GET /projects/{id}/results','Returns project evidence payload: documents, sheets, assets, clauses, and verdicts.'],
    ],[2.1,4.3]); caption(doc,'Table 2. Representative API request and response flows')
    heading(doc,'3.2 Implementation Details',2)
    para(doc,'The domain model is explicit. app/schemas/floor_plan.py defines Room, WallSegment, DoorPlacement, WindowPlacement, FloorPlanSpec, GeneratedFloorPlan, ComplianceViolation, and ComplianceResult. Geometry helpers use Shapely polygons to compute buildable envelopes, clipping, merging, splitting, areas, and cleanup. cleanup_pipeline snaps geometry to a grid, aligns walls, removes overlaps, closes gaps, checks containment, and extracts wall segments. The rule-based parser and constraint solver provide a deterministic generation path. Optional adapters for LLM providers, HouseDiffusion, GS-Diff, ChatHouseDiffusion, Modal, and local models are separated into their own modules and require configured providers, weights, or GPU services.')
    para(doc,'The compliance model is graph-oriented. planlint/models.py defines PhysicalAsset, RegulationClause, Constraint, CheckResult, and RunEvent. GraphRepository stores Project, Document, Sheet, PhysicalAsset, Regulation, Constraint, Detail, Spec, and Blueprint nodes, plus relationships such as HAS_SHEET, CONTAINS, HAS_CLAUSE, DEFINES, REFERENCES, SPECIFIED_BY, DETAILED_BY, and audit verdict edges. The checker normalizes measurement units and supports comparison of dimensions, areas, slopes, and related parameters. It returns COMPLIES_WITH, VIOLATES, or NEEDS_REVIEW rather than letting an LLM make the verdict. A rule-extraction agent can form constraints from text, but the final calculation remains deterministic.')
    table(doc,['Graph entities','Key relationship or role'],[
        ['Project -> Document -> Sheet','Organizes user-scoped source material and individual sheet metadata.'],
        ['Sheet -> PhysicalAsset','Associates identified doors, corridors, rooms, or other assets with a sheet and measurements.'],
        ['Document -> Regulation -> Constraint','Stores clause hierarchy, embeddings, and executable constraint descriptions.'],
        ['PhysicalAsset -> Regulation','Stores COMPLIES_WITH, VIOLATES, or NEEDS_REVIEW relationships with run ID, measured value, requirement, and reason.'],
        ['Blueprint -> Project','Stores serialised generated or edited plan versions for a project.'],
    ],[2.2,4.2]); caption(doc,'Table 3. Graph entities and principal relationships')
    para(doc,'Authentication is implemented in planlint/api/auth.py. When AUTH_REQUIRED is configured, the service reads a bearer token, parses it, validates an HS256 signature against the configured secret, and obtains a Supabase user profile. When local authentication is disabled, it uses a documented deterministic developer identity for offline development. Repository methods receive user context and scope sensitive project lookups, while unauthorised project access is generally represented as a not-found result to reduce enumeration. This is a development-oriented implementation, not a security audit; token configuration, secret rotation, transport security, and deployment hardening require environment-specific review.')
    heading(doc,'3.3 Technology and Hardware Software Requirements',2)
    table(doc,['Technology','Category','Actual usage','Evidence'],[
        ['Python 3.10+','Backend language','Typed API, pipelines, geometry, ingestion, graph access, tests','backend/pyproject.toml; app and planlint modules'],
        ['FastAPI and Pydantic','API and validation','Unified API factory, routers, request/response schemas','planlint/api/main.py; app/api/routes; app/schemas'],
        ['Next.js 14, React, TypeScript','Frontend','App Router pages, dashboard, editor, project workspace','frontend/package.json; frontend/src/app'],
        ['Zustand and React Query','Client state/data access','Floor-plan/model stores and request hooks','frontend/src/stores; frontend/src/hooks'],
        ['Konva and Three.js','Visualization','2D canvas and 3D floor-plan viewer','FloorPlanCanvas.tsx; ThreeViewer.tsx'],
        ['PDF.js','Document viewer','PDF sheet rendering with overlay canvas','PlanViewer.tsx'],
        ['Neo4j 5 and Cypher','Graph persistence','Projects, documents, clauses, assets, constraints, and verdicts','planlint/graph/repository.py; docker-compose.yml'],
        ['Shapely, PyClipper, NumPy','Geometry','Spatial cleanup, envelope, polygon and adjacency operations','app/services/geometry'],
        ['ezdxf','CAD export','DXF generation from stored plan geometry','app/services/export/dxf_exporter.py'],
        ['Docker Compose','Infrastructure','Frontend, backend, Neo4j service definitions','docker-compose.yml'],
    ],[1.2,1.3,2.6,2.0]); caption(doc,'Table 4. Technology stack evidenced in the repository')
    para(doc,'The repository documents Node.js, Python, Docker, and Neo4j as development prerequisites. Optional machine-learning inference adds GPU, Modal, model-weight, and provider-key requirements. No hardware benchmark, production sizing exercise, or verified cloud deployment configuration is supplied, so this report does not prescribe a production hardware specification. The safe minimum statement is that the documented local development stack requires a compatible Node.js runtime, Python environment, Docker/Compose, and a running Neo4j instance when using the graph-backed unified application.')
    if (ROOT/'docs/images/project-page.png').exists():
        add_figure(doc,ROOT/'docs/images/project-page.png','Figure 2. Project compliance workspace screenshot retained in the repository',6.0)
    if (ROOT/'docs/images/d2-selected.png').exists():
        add_figure(doc,ROOT/'docs/images/d2-selected.png','Figure 3. Selected detail evidence screenshot retained in the repository',6.0)
    page(doc)
    heading(doc,'CHAPTER 4',1); heading(doc,'RESULTS AND DISCUSSION',1)
    para(doc,'Repository inspection establishes that the project has a substantial implemented source base: 45 Python test modules were discovered under backend/tests, a TypeScript frontend contains dedicated routes and components for the principal workflows, and Docker configuration defines frontend, backend, and Neo4j services. The source-level route catalog confirms project CRUD, document upload and retrieval, verification runs and streamed events, generation, editing, ingestion, export, model listing, and compliance-check endpoints. This is evidence of implemented interfaces and modules, not evidence that every integration was executed successfully in the present environment.')
    table(doc,['Feature','Status from code inspection','Qualification'],[
        ['Unified FastAPI service and route mounting','Implemented','Production entry point is planlint.api.main:create_app; standalone app remains for isolated use.'],
        ['Project, document, run, and evidence workflow','Implemented','Depends on a reachable Neo4j service for normal unified operation.'],
        ['Rule-based floor-plan generation and geometry cleanup','Implemented','Uses typed models, solver/parser modules, and Shapely-based helpers.'],
        ['Optional LLM generation and parsing','Partially operational','Source supports provider adapters but runtime availability depends on valid credentials/quota.'],
        ['2D editor and 3D viewer','Implemented in frontend source','Browser execution and usability were not run as part of this repository analysis.'],
        ['DXF and SVG mounted export','Implemented','Endpoint code exposes these formats; PDF is created client-side for handoff.'],
        ['IFC construction support','Partially implemented','An IFC builder and parser exist; mounted export endpoint does not expose IFC.'],
        ['OCR, VLM, Docling, embeddings, remote GPU models','Partially implemented / optional','Adapters and pipelines exist; model assets, external services, and evaluation results are not verified.'],
        ['CI/CD and deployed environment','Not verified','No workflow files or deployment evidence were found in the inspected repository.'],
    ],[2.0,1.6,2.2]); caption(doc,'Table 5. Feature verification status')
    para(doc,'The main result is therefore architectural integration rather than a numerical performance claim. The deterministic checker makes the intended audit boundary clear: an extractor may help create candidate constraints or measurements, while check() in planlint/verify/checker.py normalizes units and evaluates a parameter/operator/value constraint. The graph repository then writes a verdict relationship including run information and explanatory values. This organization makes it possible for the UI to display a measurement and a linked clause, rather than returning an unsupported natural-language conclusion.')
    para(doc,'Several limitations are visible. Both document understanding and remote generation use optional components with configuration dependencies. The graph schema and repository queries are present, but no populated Neo4j database dump or deployment logs are included. The README contains broader product claims than can be fully confirmed by the mounted routes; this report therefore treats source files, route definitions, and manifests as the primary evidence. The current workspace did not supply executed test reports, code coverage output, latency measurements, model-evaluation data, user studies, or production telemetry. To report those metrics, the student should provide reproducible test logs, CI results, a target code corpus, labelled evaluation data, and deployment monitoring evidence.')
    page(doc)
    heading(doc,'CHAPTER 5',1); heading(doc,'CONCLUSION AND FUTURE SCOPE',1)
    para(doc,'BuildWise v2 is an integrated AEC software engineering project that joins a floor-plan studio with an auditable compliance-verification workflow. The repository substantiates a unified FastAPI backend, a Next.js client, typed geometry and floor-plan representations, graph-backed project persistence, plan/codebook ingestion, deterministic constraint checking, visual plan inspection, and supported drawing export. Its important design decision is to preserve deterministic verdict logic even where LLMs or vision-based tools can assist with extraction.')
    para(doc,'The completed scope should be described carefully. The source implements interfaces and pipeline components for optional LLM, VLM, diffusion, OCR, embedding, and remote-GPU capabilities; however, it does not provide repository evidence that those services were configured, evaluated, or deployed for all advertised cases. Similarly, IFC-related source components exist, but the currently mounted export route only exposes DXF and SVG. These boundaries are not defects in the report; they identify the exact conditions required for a defensible internship submission.')
    para(doc,'Future work should prioritize a repeatable integration test environment with Neo4j, representative drawing and codebook fixtures, and recorded run results. A jurisdiction-specific rule-authoring workflow, confidence calibration for extraction, traceable human-review queues for NEEDS_REVIEW outcomes, complete endpoint exposure for supported BIM formats, stronger token verification and deployment controls, and CI/CD workflows are realistic next steps. Any future claim of regulatory conformance should be validated with domain experts, versioned source codes, and measured agreement against labelled reviews.')
    page(doc)
    heading(doc,'REFERENCES',1)
    refs=[
        '[1] Vercel. (2026). Next.js App Router documentation. https://nextjs.org/docs/app',
        '[2] FastAPI. (n.d.). First steps: Automatic interactive API documentation. https://fastapi.tiangolo.com/tutorial/first-steps/',
        '[3] Neo4j. (2026). Cypher manual: Introduction. https://neo4j.com/docs/cypher-manual/current/introduction/',
        '[4] buildingSMART International. (2026). Industry Foundation Classes (IFC). https://www.buildingsmart.org/standards/bsi-standards/industry-foundation-classes/',
        '[5] BuildWise v2 repository. (2026). README, architecture documentation, source modules, configuration, and tests inspected locally for this report. [Unpublished software repository].',
    ]
    for r in refs: para(doc,r,align=WD_ALIGN_PARAGRAPH.LEFT)
    page(doc)
    heading(doc,'INTERNSHIP COMPLETION CERTIFICATE',1)
    para(doc,'Attach the original internship completion certificate issued by [ORGANIZATION NAME] here before final submission. This repository does not provide an official certificate, organization address, supervisor designation, internship dates, signature, or seal; therefore no certificate has been fabricated in this report.')

def main():
    source_hash=hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    shutil.copy2(TEMPLATE,OUT)
    doc=Document(OUT); clear_body(doc); setup_styles(doc)
    # Preserve A4 page geometry but establish consistent readable report margins.
    for s in doc.sections:
        s.top_margin=Inches(0.85); s.bottom_margin=Inches(0.75); s.left_margin=Inches(1.0); s.right_margin=Inches(0.85)
        fp=s.footer.paragraphs[0]; fp.alignment=WD_ALIGN_PARAGRAPH.RIGHT; fp.clear(); add_page_field(fp)
    set_update_fields(doc)
    title_page(doc); front_matter(doc); toc(doc); chapters(doc)
    doc.core_properties.title='BuildWise v2 Internship Report'
    doc.core_properties.subject='Technical internship report based on the BuildWise v2 repository'
    doc.core_properties.author='[STUDENT NAME]'
    doc.save(OUT)
    assert hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()==source_hash, 'Reference template changed'
    print(OUT)

if __name__=='__main__': main()
