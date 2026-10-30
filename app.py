"""
app.py — Flask entry point for ProjectDNA
"""

import os
import shutil
import tempfile
import zipfile
import traceback

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, abort, make_response,
)
from werkzeug.utils import secure_filename

import database as db
from analyzer.pipeline import run_pipeline
from analyzer.impact_analyzer import (
    analyze_change_impact_from_result,
    compute_all_impact_summaries,
)
from analyzer.drift_detector import compare_snapshots
from analyzer.module_discovery import discover_refactoring_from_result
from analyzer.risk_analyzer import analyze_architecture_risks_from_result
from ai_architect.service import AIArchitectService

ai_architect_service = AIArchitectService()

# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'projectdna-dev-secret')
app.config['MAX_CONTENT_LENGTH'] = 80 * 1024 * 1024  # 80 MB hard limit

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAX_ZIP_BYTES      = 50 * 1024 * 1024   # 50 MB
MAX_FOLDER_BYTES   = 80 * 1024 * 1024   # 80 MB total folder upload
MAX_FOLDER_FILES   = 600                 # max files in a folder upload

ALLOWED_EXT = {'zip'}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _allowed(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


# Directories that must never be included in a folder upload
_IGNORED_UPLOAD_DIRS = {
    '.venv', 'venv', 'env', '.env',
    '__pycache__', '.git', '.hg', '.svn',
    'node_modules', 'build', 'dist',
    '.tox', '.eggs', '.mypy_cache',
    '.pytest_cache', '.ruff_cache',
    'site-packages',          # inside any venv layout
    'Scripts', 'bin', 'lib',  # venv internals on Win/Unix
    'include',
}

# Only these file extensions are accepted from a folder upload
_SAFE_UPLOAD_EXTS = {'.py', '.pyx', '.pyi'}
_SAFE_UPLOAD_NAMES = {
    'requirements.txt', 'setup.py', 'setup.cfg',
    'pyproject.toml', 'Pipfile', 'Pipfile.lock',
    'tox.ini', 'pytest.ini', '.flake8',
}


def _safe_rel_path(raw: str) -> str:
    """Sanitise a relative path — strip leading slash, remove '..' components."""
    parts = raw.replace('\\', '/').split('/')
    safe = [p for p in parts if p and p != '..']
    return '/'.join(safe) if safe else ''


def _path_is_safe(parts: list) -> bool:
    """Return True only if no directory component is in the ignored set."""
    for d in parts[:-1]:   # all but the filename
        if (d in _IGNORED_UPLOAD_DIRS
                or d.endswith('.egg-info')
                or d.endswith('.dist-info')):
            return False
    filename = parts[-1]
    ext = os.path.splitext(filename)[1].lower()
    return ext in _SAFE_UPLOAD_EXTS or filename in _SAFE_UPLOAD_NAMES


def _extract_zip(zip_path: str, dest: str) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if '..' in member or member.startswith('/'):
                raise ValueError(f'Unsafe path in zip: {member}')
        zf.extractall(dest)
    entries = os.listdir(dest)
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
        return os.path.join(dest, entries[0])
    return dest


def _clone_github(url: str, dest: str) -> str:
    import git
    try:
        git.Repo.clone_from(url, dest, depth=1)
        return dest
    except Exception as e:
        raise RuntimeError(f'Git clone failed: {e}') from e


def _reconstruct_folder(files, dest: str) -> str:
    """Save uploaded folder files into dest, skipping unsafe paths/extensions.
    Returns the effective project root (strips single top-level folder name).
    """
    top_dirs = set()
    skipped = 0
    for f in files:
        rel = _safe_rel_path(f.filename)
        if not rel:
            continue
        parts = rel.split('/')

        # Server-side safety: skip ignored dirs and non-Python files
        if not _path_is_safe(parts):
            skipped += 1
            continue

        if parts:
            top_dirs.add(parts[0])
        abs_dest = os.path.join(dest, *parts)
        os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
        f.save(abs_dest)

    if skipped:
        app.logger.info('Folder upload: skipped %d non-Python/unsafe files', skipped)

    # If a single top-level dir, use it as project root
    if len(top_dirs) == 1:
        root = os.path.join(dest, next(iter(top_dirs)))
        if os.path.isdir(root):
            return root
    return dest


# ---------------------------------------------------------------------------
# initialise DB
# ---------------------------------------------------------------------------
db.init_db()


# ---------------------------------------------------------------------------
# error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(413)
def too_large(e):
    """Flask raises 413 when MAX_CONTENT_LENGTH is exceeded."""
    if request.path == '/analyze-folder':
        return jsonify({'error': 'Upload too large. Reduce the folder size or check for .venv / node_modules being included.'}), 413
    flash('Uploaded file exceeds the size limit.', 'error')
    return redirect(url_for('index'))


@app.errorhandler(500)
def server_error(e):
    if request.path == '/analyze-folder':
        return jsonify({'error': f'Server error: {e}'}), 500
    flash(f'Server error: {e}', 'error')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    analyses = db.list_analyses()
    return render_template('index.html', analyses=analyses)


@app.route('/analyze', methods=['POST'])
def analyze():
    """Handle ZIP upload and GitHub URL (regular form POST → redirect)."""
    project_name = request.form.get('project_name', '').strip()
    if not project_name:
        flash('Project name is required.', 'error')
        return redirect(url_for('index'))

    source = request.form.get('source', 'zip')
    tmp_dir = tempfile.mkdtemp(dir=UPLOAD_FOLDER)

    try:
        if source == 'zip':
            f = request.files.get('zip_file')
            if not f or f.filename == '':
                flash('Please select a ZIP file.', 'error')
                return redirect(url_for('index'))
            if not _allowed(f.filename):
                flash('Only .zip files are accepted.', 'error')
                return redirect(url_for('index'))
            zip_path = os.path.join(tmp_dir, secure_filename(f.filename))
            f.save(zip_path)
            if os.path.getsize(zip_path) > MAX_ZIP_BYTES:
                flash('ZIP file exceeds the 50 MB limit.', 'error')
                return redirect(url_for('index'))
            project_root = _extract_zip(zip_path, os.path.join(tmp_dir, 'extracted'))

        elif source == 'github':
            github_url = request.form.get('github_url', '').strip()
            if not github_url:
                flash('Please enter a GitHub URL.', 'error')
                return redirect(url_for('index'))
            project_root = _clone_github(github_url, os.path.join(tmp_dir, 'repo'))

        else:
            flash('Unknown source type.', 'error')
            return redirect(url_for('index'))

        result = run_pipeline(project_root, project_name)
        analysis_id = db.save_analysis(project_name, result)

    except zipfile.BadZipFile:
        flash('The uploaded file is not a valid ZIP archive.', 'error')
        return redirect(url_for('index'))
    except Exception as e:
        flash(f'Analysis failed: {e}', 'error')
        traceback.print_exc()
        return redirect(url_for('index'))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return redirect(url_for('dashboard', analysis_id=analysis_id))


@app.route('/analyze-folder', methods=['POST'])
def analyze_folder():
    """Handle folder upload (fetch POST → JSON response with analysis_id)."""
    project_name = request.form.get('project_name', '').strip()
    if not project_name:
        return jsonify({'error': 'Project name is required.'}), 400

    files = request.files.getlist('folder_files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files received. Please select a folder.'}), 400

    if len(files) > MAX_FOLDER_FILES:
        return jsonify({'error': f'Too many files ({len(files)}). Limit is {MAX_FOLDER_FILES}.'}), 400

    tmp_dir = tempfile.mkdtemp(dir=UPLOAD_FOLDER)
    try:
        # Check total upload size
        total_size = 0
        for f in files:
            f.stream.seek(0, 2)
            total_size += f.stream.tell()
            f.stream.seek(0)
            if total_size > MAX_FOLDER_BYTES:
                return jsonify({'error': 'Total folder size exceeds 80 MB limit.'}), 400

        dest = os.path.join(tmp_dir, 'folder')
        os.makedirs(dest, exist_ok=True)
        project_root = _reconstruct_folder(files, dest)

        result = run_pipeline(project_root, project_name)
        analysis_id = db.save_analysis(project_name, result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return jsonify({'analysis_id': analysis_id, 'redirect': url_for('dashboard', analysis_id=analysis_id)})


@app.route('/dashboard/<int:analysis_id>')
def dashboard(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    return render_template('dashboard.html', record=record, result=record['result'])


@app.route('/architecture/<int:analysis_id>')
def architecture(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    return render_template('architecture.html', record=record, result=record['result'])


@app.route('/issues/<int:analysis_id>')
def issues(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    return render_template('issues.html', record=record, result=record['result'])


@app.route('/modules/<int:analysis_id>')
def modules(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    return render_template('modules.html', record=record, result=record['result'])


@app.route('/module/<int:analysis_id>/<path:module_path>')
def module_detail(analysis_id: int, module_path: str):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    result = record['result']
    module = next((m for m in result['modules'] if m['path'] == module_path), None)
    if module is None:
        abort(404)

    imports_graph = result.get('graph_imports', {})
    links = imports_graph.get('links', [])
    imported_by = [lnk['source'] for lnk in links if lnk['target'] == module['module_name']]
    imports_to  = [lnk['target'] for lnk in links if lnk['source'] == module['module_name']]

    return render_template(
        'module_detail.html',
        record=record, result=result, module=module,
        imported_by=imported_by, imports_to=imports_to,
    )


@app.route('/report/<int:analysis_id>')
def report(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    html = render_template('report.html', record=record, result=record['result'])
    resp = make_response(html)
    resp.headers['Content-Disposition'] = (
        f'attachment; filename="projectdna-report-{analysis_id}.html"'
    )
    return resp


@app.route('/api/graph/<int:analysis_id>')
def api_graph(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    return jsonify(record['result']['graph_imports'])


@app.route('/impact/<int:analysis_id>')
def impact_view(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    result = record['result']
    impact_summaries = result.get('impact_summaries')

    # If historical record lacks impact_summaries, synthesize or compute
    if not impact_summaries:
        impact_summaries = []
        for m in result.get('modules', []):
            mod_name = m.get('module_name')
            if mod_name:
                mod_impact = analyze_change_impact_from_result(result, mod_name)
                impact_summaries.append({
                    'module_name': mod_name,
                    'path': m.get('path', ''),
                    'layer': m.get('layer', 'other'),
                    'direct_count': len(mod_impact.get('direct_dependents', [])),
                    'transitive_count': len(mod_impact.get('transitive_dependents', [])),
                    'total_dependents': mod_impact.get('total_affected_count', 0),
                    'affected_tests_count': len(mod_impact.get('affected_tests', [])),
                    'risk_level': mod_impact.get('risk_level', 'LOW'),
                    'risk_score': mod_impact.get('risk_score', 0),
                    'explanation': mod_impact.get('explanation', ''),
                    'in_cycle': mod_impact.get('in_cycle', False),
                    'in_degree': mod_impact.get('target_info', {}).get('in_degree', 0),
                    'out_degree': mod_impact.get('target_info', {}).get('out_degree', 0),
                })
        impact_summaries.sort(key=lambda s: (s['risk_score'], s['total_dependents']), reverse=True)

    target = request.args.get('target', '').strip()
    if not target and impact_summaries:
        target = impact_summaries[0]['module_name']
    elif not target and result.get('modules'):
        target = result['modules'][0]['module_name']

    impact = analyze_change_impact_from_result(result, target) if target else None

    return render_template(
        'impact.html',
        record=record,
        result=result,
        impact_summaries=impact_summaries,
        current_target=target,
        impact=impact,
    )


@app.route('/api/impact/<int:analysis_id>')
def api_impact(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        return jsonify({'error': 'Analysis not found'}), 404
    module_name = request.args.get('module', '').strip()
    if not module_name:
        return jsonify({'error': 'Parameter "module" is required'}), 400
    impact = analyze_change_impact_from_result(record['result'], module_name)
    return jsonify(impact)


@app.route('/drift/<int:analysis_id>')
def drift_view(analysis_id: int):
    target_record = db.load_analysis(analysis_id)
    if target_record is None:
        abort(404)

    project_name = target_record['project_name']
    project_snapshots = db.list_analyses_for_project(project_name)

    # Check if specific target_id and base_id passed
    req_target_id = request.args.get('target_id', type=int)
    req_base_id = request.args.get('base_id', type=int)

    if req_target_id and req_target_id != analysis_id:
        target_record = db.load_analysis(req_target_id) or target_record

    base_record = None
    if req_base_id:
        base_record = db.load_analysis(req_base_id)
    else:
        # Default base to the next older snapshot for this project if exists
        for snap in project_snapshots:
            if snap['id'] < target_record['id']:
                base_record = db.load_analysis(snap['id'])
                break
        if base_record is None:
            # Fallback to comparing with itself or earliest
            base_record = target_record

    drift = compare_snapshots(
        base=base_record['result'],
        target=target_record['result'],
        base_label=f"#{base_record['id']} ({base_record['created_at']})",
        target_label=f"#{target_record['id']} ({target_record['created_at']})",
    ) if base_record and target_record else None

    return render_template(
        'drift.html',
        record=target_record,
        base_record=base_record,
        target_record=target_record,
        project_snapshots=project_snapshots,
        drift=drift,
    )


@app.route('/api/drift')
def api_drift():
    base_id = request.args.get('base_id', type=int)
    target_id = request.args.get('target_id', type=int)
    if not base_id or not target_id:
        return jsonify({'error': 'Both base_id and target_id are required.'}), 400

    base_record = db.load_analysis(base_id)
    target_record = db.load_analysis(target_id)
    if not base_record or not target_record:
        return jsonify({'error': 'One or both snapshot IDs not found.'}), 404

    drift = compare_snapshots(
        base=base_record['result'],
        target=target_record['result'],
        base_label=f"#{base_record['id']}",
        target_label=f"#{target_record['id']}",
    )
    return jsonify(drift)


@app.route('/discovery/<int:analysis_id>')
def discovery_view(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    result = record['result']
    recommendations = result.get('refactoring_recommendations')
    if not recommendations:
        recommendations = discover_refactoring_from_result(result)

    return render_template(
        'discovery.html',
        record=record,
        result=result,
        recommendations=recommendations,
    )


@app.route('/api/discovery/<int:analysis_id>')
def api_discovery(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        return jsonify({'error': 'Analysis not found'}), 404
    result = record['result']
    recommendations = result.get('refactoring_recommendations')
    if not recommendations:
        recommendations = discover_refactoring_from_result(result)
    return jsonify(recommendations)


@app.route('/risks/<int:analysis_id>')
def risks_view(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    result = record['result']
    risk_analysis = result.get('architecture_risks')
    if not risk_analysis:
        risk_analysis = analyze_architecture_risks_from_result(result)

    return render_template(
        'risks.html',
        record=record,
        result=result,
        risk_analysis=risk_analysis,
    )


@app.route('/api/risks/<int:analysis_id>')
def api_risks(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        return jsonify({'error': 'Analysis not found'}), 404

    # Allow custom weights via query parameters
    custom_weights = {}
    for k in ["cycles", "complexity", "coupling", "layer_violations", "test_absence"]:
        val = request.args.get(f"w_{k}", type=float)
        if val is not None:
            custom_weights[k] = val

    risk_analysis = analyze_architecture_risks_from_result(
        record['result'], weights=custom_weights if custom_weights else None
    )
    return jsonify(risk_analysis)


@app.route('/delete/<int:analysis_id>', methods=['POST'])
def delete_analysis(analysis_id: int):
    db.delete_analysis(analysis_id)
    flash('Analysis deleted.', 'info')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# AI Architect Routes
# ---------------------------------------------------------------------------
@app.route('/ai-architect/<int:analysis_id>')
def ai_architect_view(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        abort(404)
    result = record['result']
    latest_review = db.get_latest_ai_review(analysis_id)
    return render_template(
        'ai_architect.html',
        record=record,
        result=result,
        latest_review=latest_review,
        groq_available=ai_architect_service.groq_client.is_available(),
    )


@app.route('/api/ai-review/<int:analysis_id>', methods=['GET', 'POST'])
def api_ai_review(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        return jsonify({'error': 'Analysis not found'}), 404

    if request.method == 'GET':
        review = db.get_latest_ai_review(analysis_id)
        if not review:
            return jsonify({'review': None, 'message': 'No review generated yet.'})
        return jsonify(review)

    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'auto')

    review_resp = ai_architect_service.run_review(record['result'], mode=mode)
    review_dict = review_resp.to_dict()

    try:
        db.save_ai_review(analysis_id, review_dict)
    except Exception as e:
        app.logger.warning(f"Could not persist review to DB: {e}")

    return jsonify(review_dict)


@app.route('/api/ai-plan/<int:analysis_id>', methods=['POST'])
def api_ai_plan(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        return jsonify({'error': 'Analysis not found'}), 404

    data = request.get_json(silent=True) or {}
    proposed_change = str(data.get('proposed_change', '')).strip()
    if not proposed_change:
        return jsonify({'error': 'proposed_change is required'}), 400

    mode = data.get('mode', 'auto')
    plan_resp = ai_architect_service.plan_change(proposed_change, record['result'], mode=mode)
    return jsonify(plan_resp.to_dict())


@app.route('/api/ai-ask/<int:analysis_id>', methods=['POST'])
def api_ai_ask(analysis_id: int):
    record = db.load_analysis(analysis_id)
    if record is None:
        return jsonify({'error': 'Analysis not found'}), 404

    data = request.get_json(silent=True) or {}
    question = str(data.get('question', '')).strip()
    if not question:
        return jsonify({'error': 'question is required'}), 400

    mode = data.get('mode', 'auto')
    ask_resp = ai_architect_service.ask_question(question, record['result'], mode=mode)
    return jsonify(ask_resp.to_dict())


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
