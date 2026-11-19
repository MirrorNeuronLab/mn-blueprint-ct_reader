"""Research-only CT report policy and publication from a verified native result."""

import hashlib
import json
from mn_sdk.blueprint.hooks import fingerprint, report_activity
from mn_sdk.blueprint.context import source_manifest
from mn_sdk.blueprint.memory import context_memory_section_enabled
from mn_sdk.blueprint.outputs import publish_artifact_bundle
from mn_sdk_common.publication import ArtifactWrite

NOTICE = 'Research and education only. Not for real diagnosis, treatment, or clinical decisions. Model output may be incorrect.'
LIMITATIONS = ['Only the anatomy-aware model crop is interpreted; the preview shows the original volume.',
    'No clinical validation, calibrated confidence, or guarantee of complete scan coverage.']


def report_document(saved):
    simulated = saved['model_revision'] == 'simulated'
    return {
        'title': 'CT research report', 'identity': saved['region'].capitalize() + ' · Research only',
        'summary': 'Simulated model response for workflow testing.' if simulated else NOTICE,
        'sections': [
            {'id': 'assessment', 'title': 'Assessment', 'blocks': [
                {'type': 'warning', 'text': NOTICE},
                {'type': 'paragraph', 'text': saved['answer']}]},
            {'id': 'evidence', 'title': 'Evidence', 'blocks': [
                {'type': 'table', 'columns': ['Run record', 'Value'], 'rows': [
                    ['Selected anatomy', saved['region']], ['Model', saved['model']],
                    ['Model revision', saved['model_revision']], ['Input volume SHA-256', saved['input_sha256']]]},
                {'type': 'paragraph', 'text': 'Match the input digest and model revision to final_artifact.json when comparing research runs.'}]},
            {'id': 'coverage', 'title': 'Coverage', 'blocks': [
                *({'type': 'paragraph', 'text': item} for item in saved['limitations']),
                {'type': 'paragraph', 'text': 'Review the selected anatomy and the source volume alongside the generated wording.'}]},
        ],
    }


def publish(block, data):
    request, result = data["request"]["payload"], data["result"]
    source = request["volume_base64"]["$artifact"]
    block.store.validate(source)
    saved = {'schema': 'mn.ct.research_report.v1', 'research_only': True, 'notice': NOTICE,
        'input_sha256': source['sha256'], 'region': request['region'], 'prompt': request['prompt'],
        'model': source_manifest(__file__)['runtime']['models']['ct']['model'],
        'model_revision': result['model_revision'], 'answer': result['answer'].strip(), 'limitations': LIMITATIONS}
    captured = block.store.read_path('inputs.json')
    saved['supervisor_instruction_revision'] = fingerprint(captured.get('supervisor_instructions', []))
    report = block.store.commit('final_artifact.json', saved, kind='ct_research_report')
    markdown = '# Experimental CT report\n\n' + NOTICE + '\n\n' + saved['answer'] + \
        '\n\n## Limitations\n\n' + '\n'.join('- ' + item for item in LIMITATIONS) + '\n'
    text = block.store.commit_bytes('report.md', markdown.encode(), kind='markdown')
    writes = [
        ArtifactWrite('ct_research_report', 'final_artifact.json', block.store.read_bytes(report),
            kind='ct_research_report', authoritative=True),
        ArtifactWrite('report', 'report.md', block.store.read_bytes(text), kind='markdown'),
    ]
    if context_memory_section_enabled(block.config.get('source_context') or {}):
        writes.append(ArtifactWrite('conversation-report', 'context_sources/outputs/' + hashlib.sha256(block.run_id.encode()).hexdigest() + '/report.md',
            ('Run reference: ' + block.run_id + '\nRegion: ' + saved['region'] + '\nModel revision: ' + saved['model_revision']
             + '\nInput SHA-256: ' + saved['input_sha256'] + '\n\n' + markdown).encode(), kind='markdown'))
    publish_artifact_bundle(block, writes, receipt_path=block.root + '/publication.json')
    if block.config.get('web_ui', {}).get('enabled') is not False:
        publish_web_report(block, saved, report.sha256)
    return {'research_only': True, 'report': report.to_dict(), 'notice': NOTICE,
        'artifacts': [report.to_dict(), text.to_dict()]}


def publish_web_report(block, saved, revision):
    try:
        from mn_web_report_skill import render_report_html
        from mn_sdk_web_ui import static_run_report_handle

        page = publish_artifact_bundle(block, [
            ArtifactWrite('web_report', 'web/index.html', lambda: render_report_html(report_document(saved)).encode(),
                          kind='html', required=False, revision=revision, mode=0o644),
        ], receipt_path=block.root + '/research-preview.json')
        if page['warnings'] or page['delivery_warnings']:
            raise OSError('CT preview unavailable')
        handle = static_run_report_handle(block.store.run_dir, 'web/index.html', title='CT research report')
        receipt = publish_artifact_bundle(block, [
            ArtifactWrite('web_ui', 'web_ui.json', json.dumps(handle).encode(), kind='json', required=False, mode=0o644),
        ], receipt_path=block.root + '/research-preview-handle.json')
        if receipt['warnings'] or receipt['delivery_warnings']:
            raise OSError('CT preview handle unavailable')
    except Exception:
        block.check()
        report_activity('The research result was saved. The web report could not be published.',
                        kind='diagnostic', level='warning', step_id=block.step_id, run_dir=block.store.run_dir)
