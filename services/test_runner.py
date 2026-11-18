import time
import unittest
import sys
import io
from tests.test_suite import CloudSyncProTestSuite

def run_automated_tests():
    """
    Executes the 14 automated tests programmatically and returns
    structured JSON telemetry for the Admin Control Center.
    """
    suite = unittest.TestLoader().loadTestsFromTestCase(CloudSyncProTestSuite)
    
    test_descriptions = {
        'test_01_user_registration': 'User Registration & Password Hashing',
        'test_02_user_authentication': 'User Authentication (Valid Credentials)',
        'test_03_invalid_login': 'Invalid Login & Access Rejection',
        'test_04_file_upload': 'Physical File Upload & Metadata Registration',
        'test_05_file_download': 'File Download & Binary Integrity',
        'test_06_file_deletion': 'File Deletion & Trash Isolation',
        'test_07_duplicate_file_detection': 'Duplicate File Detection via SHA-256 Hash',
        'test_08_storage_quota_validation': 'Storage Quota Enforcement & Rejection',
        'test_09_device_registration': 'Device Fleet Registration & Status Toggle',
        'test_10_synchronization': 'Intelligent Device File Synchronization',
        'test_11_offline_synchronization': 'Offline Synchronization Queue & Reconnection',
        'test_12_conflict_detection': 'Conflict Detection & Conflicted Copy Creation',
        'test_13_version_restoration': 'Non-Destructive Version Restoration (Creates New Increment)',
        'test_14_user_data_isolation': 'Multi-Tenant User Data Isolation & Security'
    }

    start_total = time.time()
    results = []
    passed_count = 0
    failed_count = 0

    for test in suite:
        test_method_name = test._testMethodName
        readable_title = test_descriptions.get(test_method_name, test_method_name)
        
        single_suite = unittest.TestSuite()
        single_suite.addTest(test)
        
        stream = io.StringIO()
        runner = unittest.TextTestRunner(stream=stream, verbosity=2)
        
        t0 = time.time()
        res = runner.run(single_suite)
        duration_ms = round((time.time() - t0) * 1000, 1)

        is_success = res.wasSuccessful()
        if is_success:
            passed_count += 1
            status = 'PASS'
            details = 'Passed all assertions successfully.'
        else:
            failed_count += 1
            status = 'FAIL'
            details = stream.getvalue()

        results.append({
            'id': test_method_name,
            'name': readable_title,
            'status': status,
            'duration_ms': duration_ms,
            'details': details
        })

    total_time_ms = round((time.time() - start_total) * 1000, 1)

    return {
        'total': len(results),
        'passed': passed_count,
        'failed': failed_count,
        'total_duration_ms': total_time_ms,
        'tests': results
    }
