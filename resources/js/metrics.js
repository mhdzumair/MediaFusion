document.addEventListener('DOMContentLoaded', function () {
    const fetchData = async (url) => {
        const response = await fetch(url);
        return response.json();
    };

    const removeLoadingElement = (spinnerId) => {
        const spinner = document.getElementById(spinnerId);
        if (spinner) {
            spinner.remove();
        }
    };

    const renderSchedulerDetails = async () => {
        const data = await fetchData('/metrics/scrapy-schedulers');
        removeLoadingElement('schedulerChartsSkeleton');
        const container = document.getElementById('schedulerChartsContainer');
        container.innerHTML = ''; // Clear loading skeleton

        const enabledWithStats = [];
        const enabledWithoutStats = [];
        const disabledWithStats = [];
        const disabledWithoutStats = [];

        data.forEach(scheduler => {
            if (scheduler.is_scheduler_disabled) {
                if (scheduler.last_run_state) {
                    disabledWithStats.push(scheduler);
                } else {
                    disabledWithoutStats.push(scheduler);
                }
            } else {
                if (scheduler.last_run_state) {
                    enabledWithStats.push(scheduler);
                } else {
                    enabledWithoutStats.push(scheduler);
                }
            }
        });

        const createSchedulerSection = (schedulers, sectionTitle) => {
            const section = document.createElement('div');
            section.className = 'scheduler-section';
            const sectionTitleElem = document.createElement('h5');
            sectionTitleElem.textContent = sectionTitle;
            section.appendChild(sectionTitleElem);

            let row;
            schedulers.forEach((scheduler, index) => {
                if (index % 3 === 0) {
                    row = document.createElement('div');
                    row.className = 'row';
                    section.appendChild(row);
                }

                const schedulerCard = document.createElement('div');
                schedulerCard.className = 'col-lg-4 col-md-6 col-sm-12 mb-4';

                let cardClass = 'card-scheduler ';
                if (scheduler.is_scheduler_disabled) {
                    cardClass += 'disabled';
                } else if (scheduler.last_run_state === null) {
                    cardClass += 'enabled';
                } else {
                    const logCounts = {
                        info: scheduler.last_run_state.log_count_info,
                        warning: scheduler.last_run_state.log_count_warning,
                        error: scheduler.last_run_state.log_count_error
                    };

                    const maxLogCount = Math.max(logCounts.info, logCounts.warning, logCounts.error);
                    if (maxLogCount === logCounts.error) {
                        cardClass += 'log-error';
                    } else if (maxLogCount === logCounts.warning) {
                        cardClass += 'log-warning';
                    } else {
                        cardClass += 'log-info';
                    }
                }

                const lastRunState = scheduler.last_run_state ? `
                    <p>Items Scraped: ${scheduler.last_run_state.item_scraped_count}</p>
                    <p>Items Dropped: ${scheduler.last_run_state.item_dropped_count}</p>
                    <p class="text-info">Info: ${scheduler.last_run_state.log_count_info}</p>
                    <p class="text-warning">Warning: ${scheduler.last_run_state.log_count_warning}</p>
                    <p class="text-danger">Error: ${scheduler.last_run_state.log_count_error}</p>
                ` : '';

                const cardContent = `
                    <div class="${cardClass}">
                        <h5>${scheduler.name}</h5>
                        <p>Last Run: ${scheduler.time_since_last_run}</p>
                        <p>${scheduler.is_scheduler_disabled ? 'Scheduler Disabled' : `Next Schedule In: ${scheduler.next_schedule_in}`}</p>
                        ${lastRunState}
                    </div>
                `;
                schedulerCard.innerHTML = cardContent;
                row.appendChild(schedulerCard);
            });

            container.appendChild(section);
        };

        if (enabledWithStats.length > 0) {
            createSchedulerSection(enabledWithStats, 'Enabled Schedulers with Stats');
        }
        if (enabledWithoutStats.length > 0) {
            createSchedulerSection(enabledWithoutStats, 'Enabled Schedulers without Stats');
        }
        if (disabledWithStats.length > 0) {
            createSchedulerSection(disabledWithStats, 'Disabled Schedulers with Stats');
        }
        if (disabledWithoutStats.length > 0) {
            createSchedulerSection(disabledWithoutStats, 'Disabled Schedulers without Stats');
        }
    };

    const renderMetadataCountsChart = async () => {
        const data = await fetchData('/metrics/metadata');
        removeLoadingElement('metadataCountsSkeleton');
        const ctx = document.getElementById('metadataCountsChart').getContext('2d');
        new Chart(ctx, {
            type: 'pie',
            data: {
                labels: ['Movies', 'Series', 'TV Channels'],
                datasets: [{
                    label: 'Metadata Counts',
                    data: [data.movies, data.series, data.tv_channels],
                    backgroundColor: [
                        'rgba(153, 102, 255, 0.6)',
                        'rgba(54, 162, 235, 0.6)',
                        'rgba(75, 192, 192, 0.6)'
                    ],
                    borderColor: [
                        'rgba(153, 102, 255, 1)',
                        'rgba(54, 162, 235, 1)',
                        'rgba(75, 192, 192, 1)'
                    ],
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    datalabels: {
                        formatter: (value, context) => {
                            return context.chart.data.labels[context.dataIndex] + ': ' + value.toLocaleString();
                        },
                        color: '#fff',
                        anchor: 'center',
                        align: 'center',
                        offset: 0,
                        borderRadius: 4,
                        backgroundColor: (context) => {
                            return context.dataset.backgroundColor;
                        },
                        font: {
                            weight: 'bold'
                        }
                    }
                }
            }
        });
    };

    const renderTotalTorrentsCount = async () => {
        const data = await fetchData('/metrics/torrents');
        removeLoadingElement('totalTorrentsSkeleton');
        const totalTorrentsValue = document.getElementById('totalTorrentsValue');
        totalTorrentsValue.textContent = `${data.total_torrents.toLocaleString()} (${data.total_torrents_readable})`;
    };

    const renderTorrentSourcesChart = async () => {
        const data = await fetchData('/metrics/torrents/sources');
        removeLoadingElement('torrentSourcesSkeleton');
        const ctx = document.getElementById('torrentSourcesChart').getContext('2d');
        new Chart(ctx, {
            type: 'bar',
            data: {
                datasets: data.map(source => {
                    return {
                        label: source.name,
                        data: [{x: source.name, y: source.count}],
                    };
                }),
            },
            options: {
                grouped: false,
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        display: false
                    },
                    x: {
                        ticks: {
                            color: '#fff',
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return `${context.raw.y.toLocaleString()} Torrents`;
                            }
                        }
                    },
                    datalabels: {
                        align: 'start',
                        anchor: 'end',
                        rotation: -90,
                        color: '#fff',
                        formatter: (value, context) => {
                            return value.y.toLocaleString();
                        },
                        font: {
                            weight: 'bold'
                        },
                    }
                }
            }
        });
    };

    const renderDebridCacheMetrics = async () => {
        const data = await fetchData('/metrics/debrid-cache');

        // Remove loading skeletons
        removeLoadingElement('debridTotalsSkeleton');
        removeLoadingElement('debridMemorySkeleton');
        removeLoadingElement('debridChartSkeleton');

        // Update total values
        const totalCachedTorrents = document.getElementById('totalCachedTorrentsValue');
        totalCachedTorrents.textContent = data.total_cached_torrents.toLocaleString();

        const totalMemoryUsage = document.getElementById('totalCacheMemoryValue');
        totalMemoryUsage.textContent = data.total_memory_usage_human;

        // Create the stacked bar chart for service comparison
        const ctx = document.getElementById('debridCacheChart').getContext('2d');

        // Prepare data for the chart
        const services = Object.keys(data.services);
        const cachedTorrents = services.map(service => data.services[service].cached_torrents);
        const memoryUsages = services.map(service => data.services[service].memory_usage / (1024 * 1024)); // Convert to MB

        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: services,
                datasets: [
                    {
                        label: 'Cached Torrents',
                        data: cachedTorrents,
                        backgroundColor: 'rgba(54, 162, 235, 0.6)',
                        borderColor: 'rgba(54, 162, 235, 1)',
                        borderWidth: 1,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Memory Usage (MB)',
                        data: memoryUsages,
                        backgroundColor: 'rgba(255, 99, 132, 0.6)',
                        borderColor: 'rgba(255, 99, 132, 1)',
                        borderWidth: 1,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                scales: {
                    x: {
                        ticks: {
                            color: '#fff'
                        }
                    },
                    y: {
                        type: 'linear',
                        display: true,
                        position: 'left',
                        title: {
                            display: true,
                            text: 'Cached Torrents',
                            color: '#fff'
                        },
                        ticks: {
                            color: '#fff'
                        }
                    },
                    y1: {
                        type: 'linear',
                        display: true,
                        position: 'right',
                        title: {
                            display: true,
                            text: 'Memory Usage (MB)',
                            color: '#fff'
                        },
                        ticks: {
                            color: '#fff'
                        },
                        grid: {
                            drawOnChartArea: false
                        }
                    }
                },
                plugins: {
                    legend: {
                        labels: {
                            color: '#fff'
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                if (context.datasetIndex === 0) {
                                    return `Cached Torrents: ${context.raw.toLocaleString()}`;
                                } else {
                                    return `Memory Usage: ${context.raw.toFixed(2)} MB`;
                                }
                            }
                        }
                    }
                }
            }
        });
    };

    const initCharts = () => {
        renderSchedulerDetails();
        renderMetadataCountsChart();
        renderTotalTorrentsCount();
        renderTorrentSourcesChart();
        renderDebridCacheMetrics();
    };

    Chart.register(ChartDataLabels);
    initCharts();
});
