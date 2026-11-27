"""Dictation UI contract with fake microphone audio and a controlled recognizer."""
import argparse
import json
from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('base')
args = parser.parse_args()

with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', args=['--no-proxy-server', '--disable-gpu', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    context.add_init_script("""
      const n=Math.floor(Date.now()/1000);
      localStorage.setItem('auth_session',JSON.stringify({access_token:'test.'+btoa(JSON.stringify({sub:'fixture',type:'access',iat:n,exp:n+3600,session_exp:n+3600})).replace(/=+$/,'')+'.test',last_activity:Date.now(),locked:false}));
      window.SpeechRecognition=class {
        constructor(){window.speech=this;}
        start(){setTimeout(()=>this.onstart?.(),20);}
        stop(){setTimeout(()=>this.onend?.(),250);}
        abort(){this.onend?.();}
      };
      const get=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async (...args)=>{const s=await get(...args);window.micTracks=s.getTracks();return s;};
    """)
    def api(route):
        url = route.request.url
        data = {'items': []}
        if '/users/me/context' in url:
            data = {'user': {'id': 'fixture', 'email': 'alex@example.test', 'full_name': 'Alex', 'is_superuser': True}, 'tenants': [], 'teams': []}
        elif '/scout/chats' in url:
            data = [] if route.request.method == 'GET' else {**route.request.post_data_json, 'revision': 1}
        elif '/agents/catalog' in url:
            data = {'agents': [{'id': 'agent', 'name': 'Assistant', 'harness': 'assistant', 'enabled': True}]}
        route.fulfill(content_type='application/json', body=json.dumps(data))
    context.route('**/api/**', api)
    context.route_web_socket('**/ws**', lambda ws: ws.on_message(lambda _: None))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.base + '/scout', wait_until='domcontentloaded')
    field = page.get_by_role('textbox', name='Message Scout', exact=True, include_hidden=True)
    field.fill('Collect')
    mic = page.get_by_role('button', name='Dictate', exact=True)
    expect(mic.locator('[data-codex-icon="codex-dictation-BofC"]')).to_have_count(1)
    mic.hover()
    expect(page.get_by_role('tooltip')).to_contain_text('Ctrl+Shift+D')
    mic.click()
    recording = page.get_by_role('group', name='Dictation', exact=True)
    expect(recording.get_by_label('Microphone waveform', exact=True)).to_be_visible()
    page.wait_for_function("document.querySelector('canvas[aria-label=\"Microphone waveform\"]')?.width>1")
    page.evaluate("speech.onresult({results:[{0:{transcript:'public articles'}}]})")
    expect(field).to_have_value('Collect')
    recording.get_by_role('button', name='Finish dictation', exact=True).click()
    expect(recording.get_by_role('status')).to_have_text('Transcribing')
    expect(field).to_have_value('Collect public articles')
    expect(recording).to_have_count(0)
    assert page.evaluate("micTracks.every(t=>t.readyState==='ended')")
    page.keyboard.press('Control+Shift+d')
    expect(recording.get_by_label('Microphone waveform', exact=True)).to_be_visible()
    page.evaluate("speech.onresult({results:[{0:{transcript:'discard this'}}]})")
    page.keyboard.press('Escape')
    expect(recording).to_have_count(0)
    expect(field).to_have_value('Collect public articles')
    assert page.evaluate("micTracks.every(t=>t.readyState==='ended')")
    mic.click()
    expect(recording.get_by_label('Microphone waveform', exact=True)).to_be_visible()
    page.evaluate("speech.onerror({error:'network'})")
    expect(page.get_by_role('alert')).to_contain_text('Transcription could not connect')
    expect(page.get_by_role('button', name='Retry dictation', exact=True)).to_be_visible()
    assert page.evaluate("micTracks.every(t=>t.readyState==='ended')")
    page.get_by_role('button', name='Retry dictation', exact=True).click()
    expect(recording.get_by_label('Microphone waveform', exact=True)).to_be_visible()
    page.evaluate("speech.onresult({results:[{0:{transcript:'from this site'}}]})")
    recording.get_by_role('button', name='Transcribe and send', exact=True).click()
    expect(page.locator('.scout-user-message').last).to_contain_text('Collect public articles from this site')
    page.get_by_role('button', name='Stop response', exact=True).click()
    assert not errors, errors
    browser.close()
    print('Native microphone, tooltip, shortcut, waveform, finish, cancel, retry, send and stream cleanup passed')
