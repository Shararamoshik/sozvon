def test_instance_lock_prevents_second_owner(tmp_path):
    from sozvon.instance import InstanceLock
    first = InstanceLock(tmp_path)
    second = InstanceLock(tmp_path)
    assert first.acquire()
    assert not second.acquire()
    first.close()
    assert second.acquire()
    second.close()
