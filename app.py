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


@app.route('/delete/<int:analysis_id>', methods=['POST'])
def delete_analysis(analysis_id: int):
    db.delete_analysis(analysis_id)
    flash('Analysis deleted.', 'info')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
