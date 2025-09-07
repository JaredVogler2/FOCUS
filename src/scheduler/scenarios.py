# src/scheduler/scenarios.py

from collections import defaultdict
import copy
import random
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main import ProductionScheduler

def scenario_1_csv_headcount(scheduler):
    """Scenario 1: Use CSV-defined headcount"""
    print("\n" + "=" * 80)
    print("SCENARIO 1: Scheduling with CSV-defined Headcount")
    print("=" * 80)

    total_mechanics = sum(scheduler.team_capacity.values())
    total_quality = sum(scheduler.quality_team_capacity.values())

    print(f"\nTask Structure:")
    task_type_counts = defaultdict(int)
    for task_info in scheduler.tasks.values():
        task_type_counts[task_info['task_type']] += 1

    for task_type, count in sorted(task_type_counts.items()):
        print(f"- {task_type}: {count} instances")

    print(f"- Total workforce: {total_mechanics + total_quality}")

    priority_list = scheduler.generate_global_priority_list(allow_late_delivery=True)
    makespan = scheduler.calculate_makespan()
    metrics = scheduler.calculate_lateness_metrics()

    print(f"\nMakespan: {makespan} working days")
    print("\nDelivery Analysis:")
    print("-" * 80)

    total_late_days = 0
    for product, data in sorted(metrics.items()):
        if data['projected_completion'] is not None:
            status = "ON TIME" if data['on_time'] else f"LATE by {data['lateness_days']} days"
            print(f"{product}: Due {data['delivery_date'].strftime('%Y-%m-%d')}, "
                  f"Projected {data['projected_completion'].strftime('%Y-%m-%d')} - {status}")
            print(f"  Tasks: {data['total_tasks']} total - {data['task_breakdown']}")
            if data['lateness_days'] > 0:
                total_late_days += data['lateness_days']
        else:
            print(f"{product}: UNSCHEDULED")

    return {
        'makespan': makespan,
        'metrics': metrics,
        'priority_list': priority_list,
        'team_capacities': dict(scheduler.team_capacity),
        'quality_capacities': dict(scheduler.quality_team_capacity),
        'total_late_days': total_late_days
    }


def scenario_2_minimize_makespan(scheduler, min_mechanics=1, max_mechanics=100,
                                     min_quality=1, max_quality=50):
    """
    Scenario 2: Find uniform headcount that minimizes makespan using binary search
    """
    print("\n" + "=" * 80)
    print("SCENARIO 2: Minimize Makespan with Uniform Capacity")
    print("=" * 80)

    # Store original capacities
    original_team = scheduler._original_team_capacity.copy()
    original_quality = scheduler._original_quality_capacity.copy()

    best_makespan = float('inf')
    best_config = None
    best_metrics = None

    print(f"\nSearching uniform capacities:")
    print(f"  Mechanics: {min_mechanics} to {max_mechanics}")
    print(f"  Quality: {min_quality} to {max_quality}")

    # Binary search for mechanics
    mech_low, mech_high = min_mechanics, max_mechanics
    qual_low, qual_high = min_quality, max_quality

    iterations = 0
    max_iterations = 20  # Limit binary search iterations

    while iterations < max_iterations:
        iterations += 1

        # Try middle values
        mech_capacity = (mech_low + mech_high) // 2
        qual_capacity = (qual_low + qual_high) // 2

        print(f"\n  Testing: Mechanics={mech_capacity}, Quality={qual_capacity}")

        # Set uniform capacities
        for team in scheduler.team_capacity:
            scheduler.team_capacity[team] = mech_capacity
        for team in scheduler.quality_team_capacity:
            scheduler.quality_team_capacity[team] = qual_capacity

        # Clear previous schedule
        scheduler.task_schedule = {}
        scheduler._critical_path_cache = {}

        # Try to schedule
        try:
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)

            scheduled_count = len(scheduler.task_schedule)
            total_tasks = len(scheduler.tasks)

            if scheduled_count == total_tasks:
                # Complete schedule achieved
                makespan = scheduler.calculate_makespan()

                print(f"    SUCCESS: Makespan={makespan} days")

                if makespan < best_makespan:
                    best_makespan = makespan
                    best_config = {
                        'mechanics': mech_capacity,
                        'quality': qual_capacity
                    }
                    best_metrics = scheduler.calculate_lateness_metrics()
                    print(f"    NEW BEST!")

                # Try to reduce capacity
                mech_high = mech_capacity - 1
                qual_high = qual_capacity - 1

            else:
                # Failed to schedule all tasks - need more capacity
                print(f"    INCOMPLETE: Only {scheduled_count}/{total_tasks} scheduled")
                mech_low = mech_capacity + 1
                qual_low = qual_capacity + 1

        except Exception as e:
            print(f"    ERROR: {str(e)}")
            # Need more capacity
            mech_low = mech_capacity + 1
            qual_low = qual_capacity + 1

        # Check if search space exhausted
        if mech_low > mech_high or qual_low > qual_high:
            break

    # Restore original capacities
    for team, capacity in original_team.items():
        scheduler.team_capacity[team] = capacity
    for team, capacity in original_quality.items():
        scheduler.quality_team_capacity[team] = capacity

    if best_config:
        print(f"\n" + "=" * 80)
        print("SCENARIO 2 RESULTS")
        print("=" * 80)
        print(f"Optimal uniform capacity: Mechanics={best_config['mechanics']}, Quality={best_config['quality']}")
        print(f"Makespan: {best_makespan} days")

        return {
            'optimal_mechanics': best_config['mechanics'],
            'optimal_quality': best_config['quality'],
            'makespan': best_makespan,
            'metrics': best_metrics,
            'priority_list': [],  # Would need to regenerate
            'total_headcount': (best_config['mechanics'] * len(scheduler.team_capacity) +
                                best_config['quality'] * len(scheduler.quality_team_capacity))
        }

    return None

def scenario_3_simulated_annealing(scheduler, target_earliness=-1, max_iterations=300,
                                   initial_temp=100, cooling_rate=0.95):
    """Use simulated annealing to optimize for target delivery date"""

    print("\n" + "=" * 80)
    print("SCENARIO 3: Simulated Annealing Optimization")
    print("=" * 80)
    print(f"Target: All products {abs(target_earliness)} day(s) early")

    # Store originals
    original_team = scheduler._original_team_capacity.copy()
    original_quality = scheduler._original_quality_capacity.copy()

    # Store the target for use in other methods
    scheduler.target_earliness = target_earliness

    # Initialize with moderate capacity
    current_config = initialize_moderate_capacity(scheduler)
    best_config = copy_configuration(current_config)
    best_score = float('inf')
    best_metrics = None

    temperature = initial_temp
    no_improvement = 0

    for iteration in range(max_iterations):
        # Apply configuration and schedule
        apply_capacity_configuration(scheduler, current_config)
        scheduler.task_schedule = {}
        scheduler._critical_path_cache = {}

        try:
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
            metrics = evaluate_delivery_performance(scheduler)

            # Calculate score with heavy weight on distance from target
            distance = abs(metrics['max_lateness'] - target_earliness)
            current_score = (distance ** 2) * 1000  # Quadratic penalty for distance

            if metrics['scheduled_tasks'] < metrics['total_tasks']:
                current_score += (metrics['total_tasks'] - metrics['scheduled_tasks']) * 5000

            # Add workforce penalty only if close to target
            if distance <= 2:
                current_score += metrics['total_workforce'] * 5

            # Check if we should accept this solution
            if current_score < best_score:
                best_score = current_score
                best_config = copy_configuration(current_config)
                best_metrics = metrics.copy()
                no_improvement = 0

                print(f"\n  Iteration {iteration}: NEW BEST!")
                print(f"    Lateness: {metrics['max_lateness']} (target: {target_earliness})")
                print(f"    Distance: {distance} days")
                print(f"    Workforce: {metrics['total_workforce']}")

                if distance == 0:
                    print(f"  ✓ TARGET ACHIEVED!")
                    if iteration > 50:  # Give some time for refinement
                        break
            else:
                # Probabilistic acceptance of worse solution
                delta = current_score - best_score
                probability = math.exp(-delta / temperature) if temperature > 0 else 0

                if random.random() < probability:
                    # Accept worse solution
                    no_improvement = 0
                else:
                    # Reject - revert to best
                    current_config = copy_configuration(best_config)
                    no_improvement += 1

            # Make neighbor solution
            if metrics['scheduled_tasks'] < metrics['total_tasks']:
                # Focus on fixing unscheduled tasks
                current_config = fix_unscheduled_tasks(scheduler, current_config)
            else:
                # Adjust based on distance from target
                if metrics['max_lateness'] < target_earliness:
                    # Too early - reduce capacity
                    current_config = reduce_random_teams(current_config,
                                                              min(5, abs(distance)))
                elif metrics['max_lateness'] > target_earliness:
                    # Too late - increase capacity
                    current_config = increase_random_teams(current_config,
                                                                min(5, distance + 1))
                else:
                    # At target - fine tune workforce
                    current_config = fine_tune_workforce(scheduler, current_config)

            # Cool down
            temperature *= cooling_rate

            # Reheat if stuck
            if no_improvement > 30:
                temperature = initial_temp * 0.5  # Reheat to half
                no_improvement = 0
                print(f"  Reheating at iteration {iteration}")

        except Exception as e:
            print(f"  Iteration {iteration}: Scheduling failed - adjusting capacity")
            current_config = increase_all_capacity(current_config, 2)

    # Restore original capacities
    for team, capacity in original_team.items():
        scheduler.team_capacity[team] = capacity
    for team, capacity in original_quality.items():
        scheduler.quality_team_capacity[team] = capacity

    if best_metrics and best_metrics['scheduled_tasks'] == best_metrics['total_tasks']:
        return {
            'config': best_config,
            'metrics': best_metrics,
            'total_workforce': best_metrics['total_workforce'] if best_metrics else None,
            'max_lateness': best_metrics['max_lateness'] if best_metrics else None
        }
    else:
        print("\n" + "!" * 80)
        print("! SCENARIO 3 FAILED: Could not find a valid schedule for any configuration.")
        print("!" * 80)
        return None


def scenario_3_smart_optimization(scheduler, target_earliness=-1, max_iterations=500):
    """
    Scenario 3: Optimize team capacities to achieve target delivery (1 day early)
    Start with adequate capacity and optimize to hit target precisely
    """
    print("\n" + "=" * 80)
    print("SCENARIO 3: Smart Optimization for Target Delivery")
    print("=" * 80)
    print(f"Target: All products {abs(target_earliness)} day(s) early")

    # Store originals
    original_team = scheduler._original_team_capacity.copy()
    original_quality = scheduler._original_quality_capacity.copy()

    # Store target for use in helper methods
    scheduler.target_earliness = target_earliness

    # CRITICAL: Find minimum requirements first
    min_requirements = {}
    for task_id, task_info in scheduler.tasks.items():
        team = task_info.get('team_skill', task_info.get('team'))
        if team:
            mechanics_needed = task_info.get('mechanics_required', 1)
            min_requirements[team] = max(min_requirements.get(team, 0), mechanics_needed)

    # Initialize with at least minimum requirements
    current_config = {
        'mechanic': {},
        'quality': {}
    }

    # Set initial capacities to meet ALL requirements
    for team in original_team:
        # Start with at least what's needed, or moderate default
        min_needed = min_requirements.get(team, 2)
        current_config['mechanic'][team] = max(min_needed + 2, 5)  # Buffer above minimum

    for team in original_quality:
        min_needed = min_requirements.get(team, 1)
        current_config['quality'][team] = max(min_needed + 1, 3)  # Buffer above minimum

    print(f"\nMinimum requirements found:")
    sample_reqs = list(min_requirements.items())[:5]
    for team, req in sample_reqs:
        print(f"  {team}: needs at least {req} people")

    print(f"\nStarting configuration:")
    print(f"  Mechanic teams: {len(current_config['mechanic'])} teams")
    print(f"  Quality teams: {len(current_config['quality'])} teams")
    print(
        f"  Initial workforce: {sum(current_config['mechanic'].values()) + sum(current_config['quality'].values())}")

    best_config = None
    best_score = float('inf')
    best_metrics = None

    # Track optimization progress
    iteration_history = []
    no_improvement_count = 0
    stuck_count = 0
    last_max_lateness = None
    consecutive_failures = 0

    for iteration in range(max_iterations):
        # Apply current configuration
        apply_capacity_configuration(scheduler, current_config)

        # Clear caches and schedule
        scheduler.task_schedule = {}
        scheduler._critical_path_cache = {}

        # Schedule silently
        try:
            scheduler.generate_global_priority_list(allow_late_delivery=True, silent_mode=True)
            consecutive_failures = 0  # Reset failure counter
        except Exception as e:
            consecutive_failures += 1
            print(f"  Iteration {iteration}: Scheduling failed ({consecutive_failures} consecutive failures)")

            # Ensure all teams meet minimum requirements
            for team, min_req in min_requirements.items():
                if 'Quality' in team:
                    if current_config['quality'].get(team, 0) < min_req:
                        current_config['quality'][team] = min_req + 1
                else:
                    if current_config['mechanic'].get(team, 0) < min_req:
                        current_config['mechanic'][team] = min_req + 1

            # If repeated failures, increase all capacities
            if consecutive_failures > 3:
                for team in current_config['mechanic']:
                    current_config['mechanic'][team] += 2
                for team in current_config['quality']:
                    current_config['quality'][team] += 1
                consecutive_failures = 0
            continue

        # Evaluate performance
        metrics = evaluate_delivery_performance(scheduler)

        # Check if we're stuck at the same lateness
        if last_max_lateness == metrics['max_lateness']:
            stuck_count += 1
        else:
            stuck_count = 0
        last_max_lateness = metrics['max_lateness']

        # Calculate optimization score
        score = calculate_optimization_score(scheduler, metrics, target_earliness)

        # Track progress
        iteration_history.append({
            'iteration': iteration,
            'score': score,
            'max_lateness': metrics['max_lateness'],
            'total_workforce': metrics['total_workforce'],
            'scheduled_tasks': metrics['scheduled_tasks'],
            'avg_utilization': metrics['avg_utilization']
        })

        # Check if this is the best configuration so far
        if score < best_score and metrics['scheduled_tasks'] == metrics['total_tasks']:
            best_score = score
            best_config = copy_configuration(current_config)
            best_metrics = metrics.copy()
            no_improvement_count = 0

            print(f"\n  Iteration {iteration}: NEW BEST!")
            print(f"    Max lateness: {metrics['max_lateness']} days (target: {target_earliness})")
            print(f"    Distance from target: {abs(metrics['max_lateness'] - target_earliness)} days")
            print(f"    Total workforce: {metrics['total_workforce']}")
            print(f"    Utilization: {metrics['avg_utilization']:.1f}%")
            print(f"    Tasks scheduled: {metrics['scheduled_tasks']}/{metrics['total_tasks']}")

            # Only stop if we're actually at target with good utilization
            if abs(metrics['max_lateness'] - target_earliness) <= 1:
                print(f"\n  ✓ TARGET ACHIEVED! Max lateness: {metrics['max_lateness']} days")
                if metrics['avg_utilization'] > 60:
                    print(f"    With good utilization: {metrics['avg_utilization']:.1f}%")
                    break
                else:
                    print(
                        f"    But utilization low: {metrics['avg_utilization']:.1f}%, continuing to optimize workforce...")
        else:
            no_improvement_count += 1

        # Print progress every 10 iterations
        if iteration % 10 == 0:
            distance = abs(metrics['max_lateness'] - target_earliness)
            print(f"  Iteration {iteration}: Lateness={metrics['max_lateness']} (distance={distance}), "
                  f"Workforce={metrics['total_workforce']}, Scheduled={metrics['scheduled_tasks']}/{metrics['total_tasks']}")

        # If stuck for too long, make bigger changes
        if stuck_count > 15:
            print(f"    Stuck at {metrics['max_lateness']} days for {stuck_count} iterations")
            distance_from_target = abs(metrics['max_lateness'] - target_earliness)

            if distance_from_target > 20:
                # Way off target - make big changes
                print(f"    Making large adjustments (distance: {distance_from_target} days)")
                if metrics['max_lateness'] < target_earliness:
                    # Too early - cut capacity significantly
                    for team in current_config['mechanic']:
                        if current_config['mechanic'][team] > min_requirements.get(team, 1):
                            current_config['mechanic'][team] = max(
                                min_requirements.get(team, 1),
                                int(current_config['mechanic'][team] * 0.7)
                            )
                    for team in current_config['quality']:
                        if current_config['quality'][team] > min_requirements.get(team, 1):
                            current_config['quality'][team] = max(
                                min_requirements.get(team, 1),
                                int(current_config['quality'][team] * 0.7)
                            )
                else:
                    # Too late - increase capacity
                    for team in current_config['mechanic']:
                        current_config['mechanic'][team] += 3
                    for team in current_config['quality']:
                        current_config['quality'][team] += 2
            else:
                # Make random changes to escape local optimum
                current_config = make_large_adjustment(current_config, iteration)

            stuck_count = 0
            continue

        # Don't terminate early unless actually at target
        if no_improvement_count >= 100:
            distance = abs(best_metrics.get('max_lateness', 999) - target_earliness) if best_metrics else 999
            if distance <= 2:
                print(f"\n  No improvement for 100 iterations, accepting solution {distance} days from target")
                break
            else:
                print(f"\n  No improvement for 100 iterations but still {distance} days from target")
                # Make drastic changes
                no_improvement_count = 0
                if best_metrics and best_metrics['max_lateness'] < target_earliness - 10:
                    # Still way too early - cut more aggressively
                    print(f"    Cutting capacity aggressively")
                    for team in current_config['mechanic']:
                        current_config['mechanic'][team] = max(
                            min_requirements.get(team, 1),
                            current_config['mechanic'][team] // 2
                        )
                    for team in current_config['quality']:
                        current_config['quality'][team] = max(
                            min_requirements.get(team, 1),
                            current_config['quality'][team] // 2
                        )

        # Adjust configuration based on current performance
        if metrics['scheduled_tasks'] < metrics['total_tasks']:
            # Not all tasks scheduled - increase capacity where needed
            print(f"    Only {metrics['scheduled_tasks']}/{metrics['total_tasks']} scheduled, increasing capacity")
            current_config = increase_bottleneck_capacity(scheduler, current_config)

        elif abs(metrics['max_lateness'] - target_earliness) > 20:
            # Very far from target - make aggressive changes
            days_off = abs(metrics['max_lateness'] - target_earliness)
            print(f"    {days_off} days from target, making aggressive adjustments")

            if metrics['max_lateness'] < target_earliness:
                # Too early - reduce capacity aggressively
                reduction_factor = min(0.5, days_off / 50)  # More aggressive for larger gaps
                for team in current_config['mechanic']:
                    reduction = int(current_config['mechanic'][team] * reduction_factor)
                    current_config['mechanic'][team] = max(
                        min_requirements.get(team, 1),
                        current_config['mechanic'][team] - max(1, reduction)
                    )
                for team in current_config['quality']:
                    reduction = int(current_config['quality'][team] * reduction_factor)
                    current_config['quality'][team] = max(
                        min_requirements.get(team, 1),
                        current_config['quality'][team] - max(1, reduction)
                    )
            else:
                # Too late - increase capacity
                for team in current_config['mechanic']:
                    current_config['mechanic'][team] += 2
                for team in current_config['quality']:
                    current_config['quality'][team] += 1

        elif abs(metrics['max_lateness'] - target_earliness) > 5:
            # Moderately far from target
            print(
                f"    {abs(metrics['max_lateness'] - target_earliness)} days from target, making moderate adjustments")

            if metrics['max_lateness'] < target_earliness:
                # Too early - reduce capacity moderately
                for team in current_config['mechanic']:
                    if current_config['mechanic'][team] > min_requirements.get(team, 1) + 1:
                        current_config['mechanic'][team] -= 1
                for team in current_config['quality']:
                    if current_config['quality'][team] > min_requirements.get(team, 1):
                        current_config['quality'][team] = max(
                            min_requirements.get(team, 1),
                            current_config['quality'][team] - 1
                        )
            else:
                # Too late - increase capacity moderately
                current_config = increase_critical_capacity(scheduler, current_config, metrics)

        else:
            # Close to target - fine tune
            if metrics['avg_utilization'] < 50:
                # Low utilization - try to reduce workforce
                current_config = reduce_lowest_utilization_team(scheduler, current_config)
            elif metrics['avg_utilization'] > 85:
                # High utilization - might need more capacity
                current_config = increase_highest_utilization_team(scheduler, current_config)
            else:
                # Make small adjustments
                current_config = make_small_adjustment(current_config, iteration)

    # Restore original capacities
    for team, capacity in original_team.items():
        scheduler.team_capacity[team] = capacity
    for team, capacity in original_quality.items():
        scheduler.quality_team_capacity[team] = capacity

    if best_config:
        print(f"\n" + "=" * 80)
        print("OPTIMIZATION COMPLETE")
        print("=" * 80)
        print(f"Best configuration found:")
        print(f"  Max lateness: {best_metrics['max_lateness']} days")
        print(f"  Target was: {target_earliness} days")
        print(f"  Distance from target: {abs(best_metrics['max_lateness'] - target_earliness)} days")
        print(f"  Total workforce: {best_metrics['total_workforce']}")
        print(f"  Average utilization: {best_metrics['avg_utilization']:.1f}%")
        print(f"  Tasks scheduled: {best_metrics['scheduled_tasks']}/{best_metrics['total_tasks']}")

        return {
            'config': best_config,
            'total_workforce': best_metrics['total_workforce'],
            'max_lateness': best_metrics['max_lateness'],
            'metrics': best_metrics,
            'perfect_count': best_metrics.get('products_on_target', 0),
            'good_count': best_metrics.get('products_early', 0),
            'acceptable_count': best_metrics.get('products_on_target', 0),
            'avg_utilization': best_metrics['avg_utilization'],
            'utilization_variance': 0
        }

    return None

def apply_capacity_configuration(scheduler, config):
    """Apply a capacity configuration to the scheduler"""
    for team, capacity in config['mechanic'].items():
        scheduler.team_capacity[team] = capacity
    for team, capacity in config['quality'].items():
        scheduler.quality_team_capacity[team] = capacity

def evaluate_delivery_performance(scheduler):
    """Evaluate how well the current schedule meets delivery targets"""
    # Use the actual lateness calculation method
    lateness_metrics = scheduler.calculate_lateness_metrics()

    # Calculate key metrics
    lateness_values = []
    products_on_target = 0
    products_early = 0

    for product, metrics in lateness_metrics.items():
        if metrics['projected_completion'] is not None and metrics['lateness_days'] < 999999:
            lateness_values.append(metrics['lateness_days'])

            if metrics['lateness_days'] <= -1:  # At least 1 day early
                products_on_target += 1
            if metrics['lateness_days'] < 0:  # Any amount early
                products_early += 1

    # Calculate actual makespan
    makespan = scheduler.calculate_makespan()

    avg_utilization = scheduler.calculate_initial_utilization(days_to_check=1)

    # Count workforce
    total_workforce = sum(scheduler.team_capacity.values()) + sum(scheduler.quality_team_capacity.values())

    # Return the ACTUAL max lateness from the metrics
    actual_max_lateness = max(lateness_values) if lateness_values else 999999

    return {
        'max_lateness': actual_max_lateness,
        'avg_lateness': sum(lateness_values) / len(lateness_values) if lateness_values else 999999,
        'min_lateness': min(lateness_values) if lateness_values else 999999,
        'products_on_target': products_on_target,
        'products_early': products_early,
        'total_workforce': total_workforce,
        'avg_utilization': avg_utilization,
        'scheduled_tasks': len(scheduler.task_schedule),
        'total_tasks': len(scheduler.tasks),
        'lateness_by_product': {p: m['lateness_days'] for p, m in lateness_metrics.items()},
        'makespan': makespan
    }


def calculate_optimization_score(scheduler, metrics, target_earliness):
    """Calculate optimization score (lower is better)"""
    # CRITICAL: Massive penalty for being far from target
    distance_from_target = abs(metrics['max_lateness'] - target_earliness)

    # Exponential penalty for distance from target
    earliness_penalty = distance_from_target ** 2 * 1000  # Quadratic penalty

    # Only care about workforce if we're close to target
    if distance_from_target <= 2:
        workforce_penalty = metrics['total_workforce'] * 10
    else:
        workforce_penalty = 0  # Don't care about workforce until we hit target

    # Utilization only matters if at target
    if distance_from_target <= 1:
        target_utilization = 75
        utilization_deviation = abs(metrics['avg_utilization'] - target_utilization)
        utilization_penalty = utilization_deviation * 5
    else:
        utilization_penalty = 0

    # Penalty for unscheduled tasks (always important)
    unscheduled_penalty = (metrics['total_tasks'] - metrics['scheduled_tasks']) * 5000

    total_score = earliness_penalty + workforce_penalty + utilization_penalty + unscheduled_penalty

    return total_score

def copy_configuration(config):
    """Create a deep copy of a configuration"""
    return {
        'mechanic': config['mechanic'].copy(),
        'quality': config['quality'].copy()
    }

def increase_bottleneck_capacity(scheduler, config):
    """Increase capacity for teams with unscheduled tasks and scheduling failures"""
    new_config = copy_configuration(config)

    # Find ALL teams that need more capacity
    unscheduled_by_team = {}
    max_required_by_team = {}
    task_count_by_team = {}

    # Analyze all tasks
    for task_id, task_info in scheduler.tasks.items():
        team = task_info.get('team_skill', task_info.get('team'))
        if not team:
            continue

        mechanics_needed = task_info.get('mechanics_required', 1)

        # Track maximum requirement
        if team not in max_required_by_team:
            max_required_by_team[team] = mechanics_needed
        else:
            max_required_by_team[team] = max(max_required_by_team[team], mechanics_needed)

        # Count total tasks per team
        task_count_by_team[team] = task_count_by_team.get(team, 0) + 1

        # Count unscheduled tasks
        if task_id not in scheduler.task_schedule:
            unscheduled_by_team[team] = unscheduled_by_team.get(team, 0) + 1

    # Calculate workload density for each team
    workload_density = {}
    for team, task_count in task_count_by_team.items():
        # Estimate total work minutes for this team
        total_minutes = 0
        for task_id, task_info in scheduler.tasks.items():
            if task_info.get('team_skill', task_info.get('team')) == team:
                total_minutes += task_info.get('duration', 60) * task_info.get('mechanics_required', 1)

        # Calculate how many people needed for this workload over 30 days
        available_minutes_per_person = 30 * 8 * 60  # 30 days * 8 hours * 60 minutes
        people_needed = total_minutes / available_minutes_per_person
        workload_density[team] = people_needed

    # Priority 1: Teams with unscheduled tasks
    teams_updated = 0

    for team, unscheduled_count in unscheduled_by_team.items():
        if unscheduled_count > 0:
            if 'Quality' in team:
                current = new_config['quality'].get(team, 0)
                min_needed = max_required_by_team.get(team, 1)

                # Calculate ideal capacity based on workload
                ideal_capacity = max(min_needed, int(workload_density.get(team, 1) * 1.5))  # 50% buffer

                if current < ideal_capacity:
                    new_config['quality'][team] = ideal_capacity
                    teams_updated += 1
                    print(f"      {team}: {unscheduled_count} unscheduled, {current} -> {ideal_capacity} capacity")
                elif unscheduled_count > 10:  # Many unscheduled despite having capacity
                    # Add more capacity
                    new_config['quality'][team] = current + max(2, unscheduled_count // 10)
                    teams_updated += 1
                    print(
                        f"      {team}: Still has {unscheduled_count} unscheduled, increasing {current} -> {current + max(2, unscheduled_count // 10)}")
            else:
                current = new_config['mechanic'].get(team, 0)
                min_needed = max_required_by_team.get(team, 1)

                # Calculate ideal capacity based on workload
                ideal_capacity = max(min_needed, int(workload_density.get(team, 1) * 1.5))

                if current < ideal_capacity:
                    new_config['mechanic'][team] = ideal_capacity
                    teams_updated += 1
                    print(f"      {team}: {unscheduled_count} unscheduled, {current} -> {ideal_capacity} capacity")
                elif unscheduled_count > 10:
                    new_config['mechanic'][team] = current + max(2, unscheduled_count // 10)
                    teams_updated += 1
                    print(
                        f"      {team}: Still has {unscheduled_count} unscheduled, increasing {current} -> {current + max(2, unscheduled_count // 10)}")

    # Priority 2: Ensure all teams meet minimum requirements
    for team, min_required in max_required_by_team.items():
        if 'Quality' in team:
            current = new_config['quality'].get(team, 0)
            if current < min_required:
                new_config['quality'][team] = min_required + 1  # Buffer
                teams_updated += 1
                print(f"      {team}: Increasing to minimum required {min_required + 1}")
        else:
            current = new_config['mechanic'].get(team, 0)
            if current < min_required:
                new_config['mechanic'][team] = min_required + 1
                teams_updated += 1
                print(f"      {team}: Increasing to minimum required {min_required + 1}")

    # Priority 3: Special handling for known bottlenecks
    quality_bottlenecks = ['Quality Team 1', 'Quality Team 4', 'Quality Team 7', 'Quality Team 10']

    for team in quality_bottlenecks:
        if team in task_count_by_team:
            task_count = task_count_by_team[team]
            if task_count > 50:  # Heavy workload
                current = new_config['quality'].get(team, 0)
                # These teams handle many tasks, ensure adequate capacity
                min_capacity = max(5, int(workload_density.get(team, 3) * 1.2))
                if current < min_capacity:
                    new_config['quality'][team] = min_capacity
                    print(f"      {team}: High-workload team, ensuring minimum {min_capacity} capacity")

    # Priority 4: If still having failures after multiple iterations, increase all bottleneck teams
    if hasattr(scheduler, 'consecutive_failures') and scheduler.consecutive_failures > 2:
        print(f"      Multiple scheduling failures detected, increasing all bottleneck teams")
        # Sort teams by unscheduled count
        sorted_bottlenecks = sorted(unscheduled_by_team.items(), key=lambda x: x[1], reverse=True)

        for team, unscheduled_count in sorted_bottlenecks[:10]:  # Top 10 bottlenecks
            if unscheduled_count > 0:
                if 'Quality' in team:
                    current = new_config['quality'].get(team, 0)
                    # Aggressive increase for persistent failures
                    new_config['quality'][team] = max(current + 3, int(workload_density.get(team, 2) * 2))
                    print(f"      {team}: Aggressive increase due to repeated failures")
                else:
                    current = new_config['mechanic'].get(team, 0)
                    new_config['mechanic'][team] = max(current + 3, int(workload_density.get(team, 2) * 2))
                    print(f"      {team}: Aggressive increase due to repeated failures")

    if teams_updated > 0:
        print(f"      Total teams updated: {teams_updated}")

    return new_config

def increase_critical_capacity(scheduler, config, metrics):
    """Increase capacity for critical path teams"""
    new_config = copy_configuration(config)

    # Simple approach: increase all teams slightly
    for team in config['mechanic']:
        new_config['mechanic'][team] = config['mechanic'][team] + 1
    for team in config['quality']:
        new_config['quality'][team] = config['quality'][team] + 1

    return new_config

def reduce_lowest_utilization_team(scheduler, config):
    """Reduce capacity of least utilized team"""
    new_config = copy_configuration(config)

    team_utils = scheduler.calculate_team_utilizations()
    if team_utils:
        lowest_team = min(team_utils.items(), key=lambda x: x[1])[0]

        if 'Quality' in lowest_team:
            if new_config['quality'].get(lowest_team, 0) > 1:
                new_config['quality'][lowest_team] -= 1
        else:
            if new_config['mechanic'].get(lowest_team, 0) > 1:
                new_config['mechanic'][lowest_team] -= 1

    return new_config

def increase_highest_utilization_team(scheduler, config):
    """Increase capacity of most utilized team"""
    new_config = copy_configuration(config)

    team_utils = scheduler.calculate_team_utilizations()
    if team_utils:
        highest_team = max(team_utils.items(), key=lambda x: x[1])[0]

        if 'Quality' in highest_team:
            new_config['quality'][highest_team] = new_config['quality'].get(highest_team, 0) + 1
        else:
            new_config['mechanic'][highest_team] = new_config['mechanic'].get(highest_team, 0) + 1

    return new_config

def make_small_adjustment(config, iteration):
    """Make small random adjustment for exploration"""
    new_config = copy_configuration(config)

    random.seed(iteration)

    # Pick a random team to adjust
    if random.random() < 0.5 and config['mechanic']:
        team = random.choice(list(config['mechanic'].keys()))
        if random.random() < 0.5 and new_config['mechanic'][team] > 1:
            new_config['mechanic'][team] -= 1
        else:
            new_config['mechanic'][team] += 1
    elif config['quality']:
        team = random.choice(list(config['quality'].keys()))
        if random.random() < 0.5 and new_config['quality'][team] > 1:
            new_config['quality'][team] -= 1
        else:
            new_config['quality'][team] += 1

    return new_config

def make_large_adjustment(config, iteration):
    """Make larger random adjustments when stuck"""
    new_config = copy_configuration(config)

    random.seed(iteration)

    # Adjust multiple teams at once
    num_teams_to_adjust = max(3, len(config['mechanic']) // 4)

    # Randomly select teams to adjust
    mechanic_teams = random.sample(list(config['mechanic'].keys()),
                                   min(num_teams_to_adjust, len(config['mechanic'])))
    quality_teams = random.sample(list(config['quality'].keys()),
                                  min(num_teams_to_adjust // 2, len(config['quality'])))

    for team in mechanic_teams:
        if random.random() < 0.5 and new_config['mechanic'][team] > 2:
            new_config['mechanic'][team] -= 2
        else:
            new_config['mechanic'][team] += 2

    for team in quality_teams:
        if random.random() < 0.5 and new_config['quality'][team] > 1:
            new_config['quality'][team] -= 1
        else:
            new_config['quality'][team] += 1

    return new_config


def initialize_moderate_capacity(scheduler: "ProductionScheduler"):
    """Initialize with moderate capacity for all teams"""
    config = {'mechanic': {}, 'quality': {}}

    # Find minimum requirements
    min_requirements = scheduler.calculate_minimum_team_requirements()

    # Set moderate capacity (minimum + buffer)
    for team in scheduler._original_team_capacity:
        min_needed = min_requirements.get(team, 2)
        config['mechanic'][team] = max(min_needed + 2, 5)

    for team in scheduler._original_quality_capacity:
        min_needed = min_requirements.get(team, 1)
        config['quality'][team] = max(min_needed + 1, 3)

    return config

def fix_unscheduled_tasks(scheduler, config):
    """Increase capacity for teams with unscheduled tasks"""
    new_config = copy_configuration(config)

    for task_id, task_info in scheduler.tasks.items():
        if task_id not in scheduler.task_schedule:
            team = task_info.get('team_skill', task_info.get('team'))
            if team:
                if 'Quality' in team:
                    new_config['quality'][team] = new_config['quality'].get(team, 0) + 1
                else:
                    new_config['mechanic'][team] = new_config['mechanic'].get(team, 0) + 1

    return new_config

def reduce_random_teams(config, amount):
    """Randomly reduce capacity of some teams"""
    new_config = copy_configuration(config)

    teams_to_reduce = random.sample(list(config['mechanic'].keys()),
                                    min(amount, len(config['mechanic'])))
    for team in teams_to_reduce:
        if new_config['mechanic'][team] > 2:
            new_config['mechanic'][team] -= 1

    return new_config

def increase_random_teams(config, amount):
    """Randomly increase capacity of some teams"""
    new_config = copy_configuration(config)

    teams_to_increase = random.sample(list(config['mechanic'].keys()),
                                      min(amount, len(config['mechanic'])))
    for team in teams_to_increase:
        new_config['mechanic'][team] += 1

    return new_config

def fine_tune_workforce(scheduler, config):
    """Fine tune by adjusting lowest utilized teams"""
    new_config = copy_configuration(config)

    # Simple adjustment - reduce a random underutilized team
    team_utils = scheduler.calculate_team_utilizations()
    if team_utils:
        # Find teams with low utilization
        low_util_teams = [t for t, u in team_utils.items() if u < 50]
        if low_util_teams:
            team = random.choice(low_util_teams)
            if 'Quality' in team and new_config['quality'].get(team, 0) > 1:
                new_config['quality'][team] -= 1
            elif team in new_config['mechanic'] and new_config['mechanic'][team] > 2:
                new_config['mechanic'][team] -= 1

    return new_config

def increase_all_capacity(config, amount):
    """Increase all team capacities by fixed amount"""
    new_config = copy_configuration(config)

    for team in new_config['mechanic']:
        new_config['mechanic'][team] += amount
    for team in new_config['quality']:
        new_config['quality'][team] += amount

    return new_config
