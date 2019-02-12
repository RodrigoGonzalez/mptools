import logging
import os
import signal
import time

import pytest

import multiprocessing as mp
import mptools._mptools
from mptools import (
    logger,
    MPQueue,
    _sleep_secs,
    SignalObject,
    init_signal,
    default_signal_handler,
    ProcWorker,
    proc_worker_wrapper,
    TimerProcWorker,
    QueueProcWorker,
    Proc,
)


def test_logger(caplog):
    mptools._mptools.start_time = mptools._mptools.start_time - 121.0
    caplog.set_level(logging.INFO)
    logger("FOO", logging.INFO, "TESTING")
    assert 'TESTING' in caplog.text
    assert 'FOO' in caplog.text
    assert ' 2:0' in caplog.text


def test_mpqueue_get():
    Q = MPQueue()

    item = Q.safe_get(None)
    assert item is None

    Q.put("ITEM1")
    Q.put("ITEM2")

    assert Q.safe_get( 0.02) == "ITEM1"
    assert Q.safe_get( 0.02) == "ITEM2"
    assert Q.safe_get( 0.02) is None
    assert Q.safe_get( None) is None

    num_left = Q.safe_close()
    assert num_left == 0


def test_queue_put():
    Q = MPQueue(2)
    assert Q.safe_put( "ITEM1")
    assert Q.safe_put( "ITEM2")
    assert not Q.safe_put( "ITEM3")

    num_left = Q.safe_close()
    assert num_left == 2


def test_drain_queue():
    Q = MPQueue()

    items = list(Q.drain())
    assert items == []

    expected = [f"ITEM{idx}" for idx in range(10)]
    for item in expected:
        Q.put(item)

    items = list(Q.drain())
    assert items == expected

    num_left = Q.safe_close()
    assert num_left == 0


def test_sleep_secs():
    assert _sleep_secs(5.0, time.time() - 1.0) == 0.0
    assert _sleep_secs(1.0, time.time() + 5.0) == 1.0

    end_time = time.time() + 4.0
    got = _sleep_secs(5.0, end_time)
    assert got <= 4.0
    assert got >= 3.7


def test_signal_handling():
    pid = os.getpid()
    evt = mp.Event()
    so = SignalObject(evt)
    init_signal(signal.SIGINT, so, KeyboardInterrupt, default_signal_handler)
    assert not so.shutdown_event.is_set()
    assert so.terminate_called == 0

    os.kill(pid, signal.SIGINT)
    assert so.terminate_called == 1
    assert so.shutdown_event.is_set()

    os.kill(pid, signal.SIGINT)
    assert so.terminate_called == 2
    assert so.shutdown_event.is_set()

    with pytest.raises(KeyboardInterrupt):
        os.kill(pid, signal.SIGINT)

    assert so.terminate_called == 3
    assert so.shutdown_event.is_set()


def test_proc_worker_bad_args():
    with pytest.raises(ValueError):
        ProcWorker("TEST", 1, 2, 3, "ARG1", "ARG2")


class ProcWorkerTest(ProcWorker):
    def init_args(self, args):
        self.args = args

    def main_func(self):
        self.log(logging.INFO, f"MAIN_FUNC: {self.args}")
        self.shutdown_event.set()


def test_proc_worker_good_args():
    pw = ProcWorkerTest("TEST", 1, 2, 3, "ARG1", "ARG2")
    assert pw.args == ('ARG1', 'ARG2')


def test_proc_worker_init_signals():
    pid = os.getpid()
    evt = mp.Event()
    pw = ProcWorker("TEST", 1, evt, 3, )
    so = pw.init_signals()

    assert not so.shutdown_event.is_set()
    assert so.terminate_called == 0

    os.kill(pid, signal.SIGINT)
    assert so.terminate_called == 1
    assert so.shutdown_event.is_set()

    os.kill(pid, signal.SIGINT)
    assert so.terminate_called == 2
    assert so.shutdown_event.is_set()

    with pytest.raises(KeyboardInterrupt):
        os.kill(pid, signal.SIGINT)

    assert so.terminate_called == 3
    assert so.shutdown_event.is_set()


def test_proc_worker_run(caplog):
    startup_evt = mp.Event()
    shutdown_evt = mp.Event()
    event_q = MPQueue()

    caplog.set_level(logging.INFO)
    pw = ProcWorkerTest("TEST", startup_evt, shutdown_evt, event_q, "ARG1", "ARG2")
    assert not startup_evt.is_set()
    assert not shutdown_evt.is_set()

    pw.run()

    assert startup_evt.is_set()
    assert shutdown_evt.is_set()
    item = event_q.safe_get()
    assert item
    assert item.msg_src == "TEST"
    assert item.msg_type == "SHUTDOWN"
    assert item.msg == "Normal"
    assert f"MAIN_FUNC: ('ARG1', 'ARG2')" in caplog.text


def _proc_worker_wrapper_helper(caplog, worker_class, args=None, expect_shutdown_evt=True, alarm_secs=1.0):
    startup_evt = mp.Event()
    shutdown_evt = mp.Event()
    event_q = MPQueue()
    if args is None:
        args = ()

    def alarm_handler(signal_num, current_stack_frame):
        shutdown_evt.set()

    if alarm_secs:
        signal.signal(signal.SIGALRM, alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, alarm_secs)
    caplog.set_level(logging.DEBUG)
    proc_worker_wrapper(worker_class, "TEST", startup_evt, shutdown_evt, event_q, *args)
    assert startup_evt.is_set()
    assert shutdown_evt.is_set() == expect_shutdown_evt
    items = list(event_q.drain())
    assert items
    last_item = items[-1]
    assert last_item.msg_src == "TEST"
    assert last_item.msg_type == "SHUTDOWN"
    assert last_item.msg == "Normal"

    return items[:-1]


def test_proc_worker_wrapper(caplog):
    items = _proc_worker_wrapper_helper(caplog, ProcWorkerTest, ("ARG1", "ARG2"))
    assert not items
    assert f"MAIN_FUNC: ('ARG1', 'ARG2')" in caplog.text


class ProcWorkerException(ProcWorker):
    def main_func(self):
        raise NameError("Because this doesn't happen often")


def test_proc_worker_exception(caplog):
    startup_evt = mp.Event()
    shutdown_evt = mp.Event()
    event_q = MPQueue()

    caplog.set_level(logging.INFO)
    with pytest.raises(NameError):
        proc_worker_wrapper(ProcWorkerException, "TEST", startup_evt, shutdown_evt, event_q)
    assert startup_evt.is_set()
    assert not shutdown_evt.is_set()
    item = event_q.safe_get()
    assert item
    assert item.msg_src == "TEST"
    assert item.msg_type == "FATAL"
    assert item.msg == "Because this doesn't happen often"

    assert f"Exception Shutdown" in caplog.text


class TimerProcWorkerTest(TimerProcWorker):
    INTERVAL_SECS = 0.01
    times_called = 0

    def main_func(self):
        self.times_called += 1
        self.event_q.put(f"TIMER {self.times_called} [{time.time()}]")
        if self.times_called >= 4:
            self.shutdown_event.set()


def test_timer_proc_worker(caplog):
    items = _proc_worker_wrapper_helper(caplog, TimerProcWorkerTest)
    assert len(items) == 4
    for idx, item in enumerate(items[:-1]):
        assert item.startswith(f'TIMER {idx + 1} [')


class QueueProcWorkerTest(QueueProcWorker):
    def main_func(self, item):
        self.event_q.put(f'DONE {item}')


def test_queue_proc_worker(caplog):
    work_q = MPQueue()
    work_q.put(1)
    work_q.put(2)
    work_q.put(3)
    work_q.put(4)
    work_q.put("END")
    work_q.put(5)

    items = _proc_worker_wrapper_helper(caplog, QueueProcWorkerTest, args=(work_q,), expect_shutdown_evt=False)
    assert len(items) == 4
    assert items == [f'DONE {idx + 1}' for idx in range(4)]


def test_proc(caplog):
    shutdown_evt = mp.Event()
    event_q = MPQueue()
    caplog.set_level(logging.INFO)
    proc = Proc("TEST", TimerProcWorkerTest, shutdown_evt, event_q)

    for idx in range(4):
        item = event_q.safe_get(1.0)
        assert item, f"idx: {idx}"
        assert item.startswith(f'TIMER {idx + 1} [')

    item = event_q.safe_get(1.0)
    assert item.msg_src == "TEST"
    assert item.msg_type == "SHUTDOWN"
    assert item.msg == "Normal"

    proc.stop(wait_time=0.5)

    assert not proc.proc.is_alive()

