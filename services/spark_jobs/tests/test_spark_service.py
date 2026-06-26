#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Spark Streaming Service Test Suite
Tests both raw streaming and aggregation pipelines
"""

import psycopg2
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import sys

# Database connection parameters
DB_PARAMS = {
    'host': 'localhost',
    'port': 5432,
    'database': 'industryflow',
    'user': 'postgres',
    'password': 'postgres'
}


class Color:
    """ANSI color codes"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """Print formatted header"""
    print(f"\n{Color.CYAN}{Color.BOLD}{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}{Color.END}\n")


def print_test(test_name: str):
    """Print test name"""
    print(f"{Color.BLUE}▶ Testing: {test_name}{Color.END}")


def print_success(message: str):
    """Print success message"""
    print(f"  {Color.GREEN}✓ {message}{Color.END}")


def print_warning(message: str):
    """Print warning message"""
    print(f"  {Color.YELLOW}⚠ {message}{Color.END}")


def print_error(message: str):
    """Print error message"""
    print(f"  {Color.RED}✗ {message}{Color.END}")


def print_info(message: str):
    """Print info message"""
    print(f"    {message}")


def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(**DB_PARAMS)


def test_raw_streaming_pipeline() -> Dict:
    """Test 1: Validate raw data streaming (kafka_to_timescaledb)"""
    print_test("Raw Streaming Pipeline (Kafka → Spark → TimescaleDB)")

    results = {
        'passed': True,
        'checks': []
    }

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Check 1: Table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'sensor_measurements'
            );
        """)
        if cur.fetchone()[0]:
            print_success("Raw measurements table exists")
            results['checks'].append(('table_exists', True))
        else:
            print_error("Raw measurements table not found")
            results['checks'].append(('table_exists', False))
            results['passed'] = False

        # Check 2: Recent data flow (last 5 minutes)
        cur.execute("""
            SELECT COUNT(*) 
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '5 minutes';
        """)
        recent_count = cur.fetchone()[0]
        if recent_count > 0:
            print_success(f"Recent data flowing: {recent_count} records in last 5 minutes")
            results['checks'].append(('recent_data', True))
        else:
            print_warning("No data in last 5 minutes - pipeline may be stopped")
            results['checks'].append(('recent_data', False))

        # Check 3: Data completeness - all required fields present
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(time) as has_time,
                COUNT(sensor_id) as has_sensor,
                COUNT(equipment_id) as has_equipment,
                COUNT(value) as has_value,
                COUNT(company_id) as has_company
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '10 minutes';
        """)
        row = cur.fetchone()
        total, has_time, has_sensor, has_equipment, has_value, has_company = row

        if total > 0:
            completeness = min(has_time, has_sensor, has_equipment, has_value, has_company) / total * 100
            if completeness == 100:
                print_success(f"Data completeness: {completeness:.1f}% (all fields present)")
                results['checks'].append(('data_completeness', True))
            else:
                print_warning(f"Data completeness: {completeness:.1f}% (missing some fields)")
                results['checks'].append(('data_completeness', False))

        # Check 4: Multi-tenant data (different companies)
        cur.execute("""
            SELECT DISTINCT company_id 
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '10 minutes'
            ORDER BY company_id;
        """)
        companies = [row[0] for row in cur.fetchall()]
        if len(companies) > 0:
            print_success(f"Multi-tenant data present: {len(companies)} companies ({', '.join(companies)})")
            results['checks'].append(('multi_tenant', True))
        else:
            print_warning("No company data found")
            results['checks'].append(('multi_tenant', False))

        # Check 5: Data distribution across sensors
        cur.execute("""
            SELECT 
                sensor_id,
                COUNT(*) as count
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '10 minutes'
            GROUP BY sensor_id
            ORDER BY count DESC;
        """)
        sensor_distribution = cur.fetchall()
        if len(sensor_distribution) > 0:
            print_success(f"Data from {len(sensor_distribution)} sensors")
            for sensor_id, count in sensor_distribution[:5]:  # Show top 5
                print_info(f"  • {sensor_id}: {count} measurements")
            results['checks'].append(('sensor_distribution', True))
        else:
            print_warning("No sensor distribution data")
            results['checks'].append(('sensor_distribution', False))

        # Check 6: Quality codes present
        cur.execute("""
            SELECT 
                quality_code,
                COUNT(*) as count
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '10 minutes'
            GROUP BY quality_code
            ORDER BY quality_code;
        """)
        quality_dist = cur.fetchall()
        if len(quality_dist) > 0:
            print_success(f"Quality codes tracked: {len(quality_dist)} levels")
            for code, count in quality_dist:
                print_info(f"  • Quality {code}: {count} measurements")
            results['checks'].append(('quality_tracking', True))
        else:
            print_warning("No quality code data")
            results['checks'].append(('quality_tracking', False))

        cur.close()
        conn.close()

    except Exception as e:
        print_error(f"Test failed: {e}")
        results['passed'] = False
        results['error'] = str(e)

    return results


def test_aggregation_pipeline() -> Dict:
    """Test 2: Validate aggregation pipeline (kafka_aggregations)"""
    print_test("Aggregation Pipeline (1min, 5min, 1hour)")

    results = {
        'passed': True,
        'checks': [],
        'aggregations': {}
    }

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Test each aggregation table
        agg_tables = [
            ('sensor_aggregations_1min', '1 minute', '10 minutes'),
            ('sensor_aggregations_5min', '5 minutes', '30 minutes'),
            ('sensor_aggregations_1hour', '1 hour', '6 hours')
        ]

        for table_name, window_size, time_range in agg_tables:
            print_info(f"\nChecking {table_name}...")

            agg_result = {
                'exists': False,
                'has_recent_data': False,
                'record_count': 0,
                'sensor_coverage': 0
            }

            # Check table exists
            cur.execute(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = '{table_name}'
                );
            """)
            if cur.fetchone()[0]:
                print_success(f"  ✓ Table exists")
                agg_result['exists'] = True
            else:
                print_error(f"  ✗ Table not found")
                results['passed'] = False
                continue

            # Check recent data
            cur.execute(f"""
                SELECT COUNT(*) 
                FROM {table_name} 
                WHERE time > NOW() - INTERVAL '{time_range}';
            """)
            count = cur.fetchone()[0]
            agg_result['record_count'] = count

            if count > 0:
                print_success(f"  ✓ Recent data: {count} aggregations in last {time_range}")
                agg_result['has_recent_data'] = True
            else:
                print_warning(f"  ⚠ No recent data in last {time_range}")

            # Check sensor coverage
            cur.execute(f"""
                SELECT COUNT(DISTINCT sensor_id) 
                FROM {table_name} 
                WHERE time > NOW() - INTERVAL '{time_range}';
            """)
            sensor_count = cur.fetchone()[0]
            agg_result['sensor_coverage'] = sensor_count

            if sensor_count > 0:
                print_success(f"  ✓ Sensor coverage: {sensor_count} sensors")
            else:
                print_warning(f"  ⚠ No sensor coverage")

            # Check aggregation quality (avg, min, max relationships)
            cur.execute(f"""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN min_value <= avg_value AND avg_value <= max_value THEN 1 END) as valid
                FROM {table_name} 
                WHERE time > NOW() - INTERVAL '{time_range}'
                AND min_value IS NOT NULL 
                AND avg_value IS NOT NULL 
                AND max_value IS NOT NULL;
            """)
            row = cur.fetchone()
            if row[0] > 0:
                validity = (row[1] / row[0]) * 100
                if validity == 100:
                    print_success(f"  ✓ Aggregation logic valid: {validity:.1f}%")
                    agg_result['logic_valid'] = True
                else:
                    print_warning(f"  ⚠ Aggregation logic issues: {validity:.1f}% valid")
                    agg_result['logic_valid'] = False

            results['aggregations'][table_name] = agg_result

        cur.close()
        conn.close()

    except Exception as e:
        print_error(f"Test failed: {e}")
        results['passed'] = False
        results['error'] = str(e)

    return results


def test_spark_performance() -> Dict:
    """Test 3: Measure Spark pipeline performance metrics"""
    print_test("Spark Pipeline Performance")

    results = {
        'passed': True,
        'metrics': {}
    }

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Metric 1: Throughput (messages per minute)
        cur.execute("""
            SELECT 
                DATE_TRUNC('minute', time) as minute,
                COUNT(*) as count
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '10 minutes'
            GROUP BY minute
            ORDER BY minute DESC
            LIMIT 10;
        """)
        throughput_data = cur.fetchall()

        if len(throughput_data) > 0:
            # Only calculate from complete minutes (exclude current partial minute)
            complete_minutes = [row[1] for row in throughput_data[1:]] if len(throughput_data) > 1 else [row[1] for row
                                                                                                         in
                                                                                                         throughput_data]

            if complete_minutes:
                avg_throughput = sum(complete_minutes) / len(complete_minutes)
                max_throughput = max(complete_minutes)
                min_throughput = min(complete_minutes)

                print_success(f"Throughput: {avg_throughput:.1f} msg/min (avg)")
                print_info(f"  Range: {min_throughput}-{max_throughput} msg/min")
                print_info(f"  Complete minutes analyzed: {len(complete_minutes)}")

                results['metrics']['avg_throughput'] = avg_throughput
                results['metrics']['max_throughput'] = max_throughput
                results['metrics']['min_throughput'] = min_throughput

                if avg_throughput >= 100:  # Expected throughput from docs
                    print_success("  ✓ Throughput meets performance target (>100 msg/min)")
                else:
                    print_warning(f"  ⚠ Throughput below target: {avg_throughput:.1f} < 100 msg/min")
            else:
                print_warning("Not enough complete minute data for throughput calculation")
        else:
            print_warning("No recent throughput data")

        # Metric 2: End-to-end latency estimate
        cur.execute("""
            SELECT 
                time,
                NOW() - time as latency
            FROM sensor_measurements 
            WHERE time > NOW() - INTERVAL '1 minute'
            ORDER BY time DESC
            LIMIT 1;
        """)
        row = cur.fetchone()
        if row:
            latency = row[1]
            latency_seconds = latency.total_seconds()
            print_success(f"Latest record latency: {latency_seconds:.2f} seconds")
            results['metrics']['latency_seconds'] = latency_seconds

            if latency_seconds < 10:
                print_success("  ✓ Latency within target (<10 seconds)")
            else:
                print_warning(f"  ⚠ High latency: {latency_seconds:.2f} seconds")

        # Metric 3: Data continuity (gaps in time series)
        cur.execute("""
            WITH time_gaps AS (
                SELECT 
                    sensor_id,
                    time,
                    LEAD(time) OVER (PARTITION BY sensor_id ORDER BY time) - time as gap
                FROM sensor_measurements 
                WHERE time > NOW() - INTERVAL '10 minutes'
            )
            SELECT 
                COUNT(*) as total_gaps,
                COUNT(CASE WHEN gap > INTERVAL '1 minute' THEN 1 END) as significant_gaps,
                MAX(gap) as max_gap
            FROM time_gaps 
            WHERE gap IS NOT NULL;
        """)
        row = cur.fetchone()
        if row and row[0]:
            total_gaps = row[0]
            significant_gaps = row[1]
            max_gap = row[2]

            if significant_gaps == 0:
                print_success(f"Data continuity: No significant gaps detected")
            else:
                print_warning(f"Data continuity: {significant_gaps} gaps > 1 minute (max: {max_gap})")

            results['metrics']['total_gaps'] = total_gaps
            results['metrics']['significant_gaps'] = significant_gaps

        cur.close()
        conn.close()

    except Exception as e:
        print_error(f"Test failed: {e}")
        results['passed'] = False
        results['error'] = str(e)

    return results


def test_spark_fault_tolerance() -> Dict:
    """Test 4: Validate Spark checkpointing and recovery"""
    print_test("Spark Fault Tolerance & Checkpointing")

    results = {
        'passed': True,
        'checks': []
    }

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Check 1: No duplicate records (exactly-once semantics)
        cur.execute("""
            WITH duplicates AS (
                SELECT 
                    sensor_id,
                    time,
                    value,
                    COUNT(*) as count
                FROM sensor_measurements 
                WHERE time > NOW() - INTERVAL '10 minutes'
                GROUP BY sensor_id, time, value
                HAVING COUNT(*) > 1
            )
            SELECT 
                COUNT(*) as duplicate_groups,
                SUM(count) as total_duplicate_records
            FROM duplicates;
        """)
        row = cur.fetchone()
        duplicate_groups = row[0] if row[0] else 0
        total_duplicates = row[1] if row[1] else 0

        if duplicate_groups == 0:
            print_success("No duplicate records detected (exactly-once processing)")
            results['checks'].append(('no_duplicates', True))
        else:
            print_warning(f"Found {total_duplicates} duplicate records in {duplicate_groups} groups")
            print_info(f"  This may indicate Spark checkpoint issues or multiple restarts")

            # Show sample duplicates
            cur.execute("""
                SELECT 
                    sensor_id,
                    time,
                    COUNT(*) as count
                FROM sensor_measurements 
                WHERE time > NOW() - INTERVAL '10 minutes'
                GROUP BY sensor_id, time
                HAVING COUNT(*) > 1
                ORDER BY count DESC
                LIMIT 3;
            """)
            samples = cur.fetchall()
            if samples:
                print_info("  Sample duplicate timestamps:")
                for sensor_id, timestamp, count in samples:
                    print_info(f"    • {sensor_id} @ {timestamp}: {count} copies")

            results['checks'].append(('no_duplicates', False))
            results['duplicate_count'] = total_duplicates

        # Check 2: Data ordering within sensor streams
        cur.execute("""
            WITH ordered_check AS (
                SELECT 
                    sensor_id,
                    time,
                    LAG(time) OVER (PARTITION BY sensor_id ORDER BY time) as prev_time,
                    CASE 
                        WHEN time < LAG(time) OVER (PARTITION BY sensor_id ORDER BY time) 
                        THEN 1 
                        ELSE 0 
                    END as out_of_order
                FROM sensor_measurements 
                WHERE time > NOW() - INTERVAL '10 minutes'
            )
            SELECT 
                COUNT(*) as total,
                SUM(out_of_order) as out_of_order_count
            FROM ordered_check 
            WHERE prev_time IS NOT NULL;
        """)
        row = cur.fetchone()
        if row and row[0] > 0:
            total, out_of_order = row
            if out_of_order == 0:
                print_success(f"Data ordering correct: 0 out-of-order records")
                results['checks'].append(('data_ordering', True))
            else:
                print_warning(f"Out-of-order records: {out_of_order}/{total}")
                results['checks'].append(('data_ordering', False))

        cur.close()
        conn.close()

    except Exception as e:
        print_error(f"Test failed: {e}")
        results['passed'] = False
        results['error'] = str(e)

    return results


def generate_summary_report(test_results: List[Tuple[str, Dict]]):
    """Generate final test summary report"""
    print_header("SPARK SERVICE TEST SUMMARY")

    total_tests = len(test_results)
    passed_tests = sum(1 for _, result in test_results if result['passed'])

    # Overall status
    print(f"\n{Color.BOLD}Overall Status:{Color.END}")
    if passed_tests == total_tests:
        print(f"  {Color.GREEN}✓ ALL TESTS PASSED ({passed_tests}/{total_tests}){Color.END}")
    else:
        print(f"  {Color.RED}✗ SOME TESTS FAILED ({passed_tests}/{total_tests}){Color.END}")

    # Individual test results
    print(f"\n{Color.BOLD}Test Results:{Color.END}")
    for test_name, result in test_results:
        status = f"{Color.GREEN}PASS{Color.END}" if result['passed'] else f"{Color.RED}FAIL{Color.END}"
        print(f"  [{status}] {test_name}")

    # Key metrics
    print(f"\n{Color.BOLD}Key Performance Metrics:{Color.END}")
    for test_name, result in test_results:
        if 'metrics' in result and result['metrics']:
            print(f"\n  {test_name}:")
            for metric, value in result['metrics'].items():
                print(f"    • {metric}: {value}")

    # Recommendations
    print(f"\n{Color.BOLD}Recommendations:{Color.END}")
    has_issues = False

    for test_name, result in test_results:
        if not result['passed']:
            has_issues = True
            print(f"  {Color.YELLOW}⚠{Color.END} {test_name} issues detected")
            if 'error' in result:
                print(f"      Error: {result['error']}")

    if not has_issues:
        print(f"  {Color.GREEN}✓ No issues detected - Spark service is healthy{Color.END}")

    print()


def main():
    """Run all Spark service tests"""
    print_header("SPARK STREAMING SERVICE TEST SUITE")

    print(f"{Color.BOLD}Target Services:{Color.END}")
    print("  • Spark Streaming (kafka_to_timescaledb.py)")
    print("  • Spark Aggregations (kafka_aggregations.py)")
    print(f"\n{Color.BOLD}Database:{Color.END} {DB_PARAMS['host']}:{DB_PARAMS['port']}/{DB_PARAMS['database']}")
    print(f"{Color.BOLD}Timestamp:{Color.END} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Run tests
    test_results = []

    try:
        # Test 1: Raw streaming pipeline
        result1 = test_raw_streaming_pipeline()
        test_results.append(("Raw Streaming Pipeline", result1))

        time.sleep(1)

        # Test 2: Aggregation pipeline
        result2 = test_aggregation_pipeline()
        test_results.append(("Aggregation Pipeline", result2))

        time.sleep(1)

        # Test 3: Performance metrics
        result3 = test_spark_performance()
        test_results.append(("Performance Metrics", result3))

        time.sleep(1)

        # Test 4: Fault tolerance
        result4 = test_spark_fault_tolerance()
        test_results.append(("Fault Tolerance", result4))

    except KeyboardInterrupt:
        print(f"\n\n{Color.YELLOW}Tests interrupted by user{Color.END}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Critical error: {e}")
        sys.exit(1)

    # Generate summary
    generate_summary_report(test_results)

    # Exit code
    all_passed = all(result['passed'] for _, result in test_results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()