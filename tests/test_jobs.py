"""任务队列测试：假 runner，不需要模型。"""

from __future__ import annotations

import time
from pathlib import Path

import mido
import pytest

from transkun_app.webui import jobs as jobs_module


def write_midi(path):
    mid = mido.MidiFile(type=0, ticks_per_beat=96)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message('note_on', note=60, velocity=80, time=0))
    track.append(mido.Message('note_off', note=60, velocity=0, time=96))
    mid.save(str(path))


def make_job(manager, name='a.wav'):
    job_id, directory = manager.new_directory()
    input_path = directory / ('input' + Path(name).suffix)
    input_path.write_bytes(b'fake audio')
    return jobs_module.Job(
        id=job_id, filename=name, model='2.0', directory=directory,
        input_path=input_path)


def wait_final(manager, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.get(job_id)
        if job and job['status'] in jobs_module.FINAL_STATUSES:
            return job
        time.sleep(0.02)
    raise AssertionError('任务超时: %s' % job_id)


def test_job_runs_and_produces_midi(tmp_path):
    def runner(job, manager):
        manager.update_progress(job, 1, 2)
        manager.update_progress(job, 2, 2)
        raw = job.directory / 'out.mid'
        write_midi(raw)
        job.raw_path = raw
        job.notes = 1
        job.pedals = 0
        job.roll = jobs_module.midi_roll(raw)

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner)
    job = make_job(manager)
    manager.submit(job)
    done = wait_final(manager, job.id)
    assert done['status'] == 'done'
    assert done['percent'] == 100
    assert done['notes'] == 1
    assert done['raw'] is True
    assert manager.path(job.id, 'raw').exists()
    assert len(done['roll']) == 1


def test_runner_failure_is_reported(tmp_path):
    def runner(job, manager):
        raise RuntimeError('boom')

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner)
    job = make_job(manager)
    manager.submit(job)
    failed = wait_final(manager, job.id)
    assert failed['status'] == 'failed'
    assert 'boom' in failed['error']


def test_cancel_queued_job(tmp_path):
    def runner(job, manager):
        time.sleep(0.5)

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner, queue_limit=2)
    blocker = make_job(manager, 'block.wav')
    manager.submit(blocker)
    queued = make_job(manager, 'queued.wav')
    manager.submit(queued)

    snapshot = manager.cancel(queued.id)
    assert snapshot['status'] == 'cancelled'
    manager.cancel(blocker.id)
    wait_final(manager, blocker.id)
    wait_final(manager, queued.id)


def test_cancel_running_job(tmp_path):
    def runner(job, manager):
        for step in range(20):
            time.sleep(0.05)
            manager.update_progress(job, step, 20)

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner)
    job = make_job(manager)
    manager.submit(job)
    deadline = time.time() + 5
    while time.time() < deadline:
        if manager.get(job.id)['status'] == 'running':
            break
        time.sleep(0.01)
    manager.cancel(job.id)
    assert wait_final(manager, job.id)['status'] == 'cancelled'


def test_queue_limit(tmp_path):
    def runner(job, manager):
        time.sleep(0.5)

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner, queue_limit=1)
    first = make_job(manager)
    manager.submit(first)
    # queue_limit 只限制「等待中」；先等第一个真正开跑，第二个才能占住队列。
    deadline = time.time() + 5
    while time.time() < deadline:
        if manager.get(first.id)['status'] == 'running':
            break
        time.sleep(0.01)
    second = make_job(manager)
    manager.submit(second)
    third = make_job(manager)
    with pytest.raises(jobs_module.QueueFull):
        manager.submit(third)
    manager.cancel(first.id)
    manager.cancel(second.id)
    wait_final(manager, first.id)
    wait_final(manager, second.id)


def test_retry_reuses_input(tmp_path):
    def runner(job, manager):
        raw = job.directory / 'out.mid'
        write_midi(raw)
        job.raw_path = raw

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner)
    first = make_job(manager)
    manager.submit(first)
    wait_final(manager, first.id)

    snapshot = manager.clone_for_retry(first.id, '2.0', None)
    second_id = snapshot['id']
    wait_final(manager, second_id)
    assert manager._jobs[first.id].input_path.stat().st_ino == \
        manager._jobs[second_id].input_path.stat().st_ino


def test_delete_running_job_is_refused(tmp_path):
    def runner(job, manager):
        time.sleep(0.3)

    manager = jobs_module.JobManager(tmp_path / 'jobs', runner)
    job = make_job(manager)
    manager.submit(job)
    deadline = time.time() + 5
    while time.time() < deadline:
        if manager.get(job.id)['status'] == 'running':
            break
        time.sleep(0.01)
    with pytest.raises(RuntimeError):
        manager.delete(job.id)
    manager.cancel(job.id)
    wait_final(manager, job.id)
