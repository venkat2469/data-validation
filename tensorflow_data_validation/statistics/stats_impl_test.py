# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for the statistics generation implementation."""

from __future__ import absolute_import
from __future__ import division

from __future__ import print_function

from absl.testing import absltest
from absl.testing import parameterized
import apache_beam as beam
from apache_beam.testing import util
import numpy as np
import pyarrow as pa
from tensorflow_data_validation import types
from tensorflow_data_validation.arrow import arrow_util
from tensorflow_data_validation.statistics import stats_impl
from tensorflow_data_validation.statistics import stats_options
from tensorflow_data_validation.statistics.generators import basic_stats_generator
from tensorflow_data_validation.statistics.generators import stats_generator
from tensorflow_data_validation.utils import slicing_util
from tensorflow_data_validation.utils import test_util
from tensorflow_data_validation.types_compat import List

from google.protobuf import text_format
from tensorflow.python.util.protobuf import compare
from tensorflow_metadata.proto.v0 import schema_pb2
from tensorflow_metadata.proto.v0 import statistics_pb2


# Testing classes for 'custom_feature_generator' testcase.
# They are defined module level in order to allow pickling.
class _BaseCounter(stats_generator.CombinerFeatureStatsGenerator):
  """A base counter implementation as CombinerFeatureStatsGenerator."""

  def __init__(self):
    super(_BaseCounter, self).__init__(type(self).__name__)

  def create_accumulator(self):
    return 0

  def merge_accumulators(self, accumulators):
    return sum(accumulators)

  def extract_output(self,
                     accumulator):
    result = statistics_pb2.FeatureNameStatistics()
    result.custom_stats.add(name=type(self).__name__, num=accumulator)
    return result


class _ValueCounter(_BaseCounter):
  """A _BaseCounter that counts number of values."""

  def add_input(self, accumulator, input_column):
    for feature_array in input_column.data.iterchunks():
      num_values = arrow_util.ListLengthsFromListArray(feature_array).to_numpy()
      none_mask = arrow_util.GetArrayNullBitmapAsByteArray(
          feature_array).to_numpy().view(np.bool)
      accumulator += np.sum(num_values[~none_mask])
    return accumulator


class _ExampleCounter(_BaseCounter):
  """A _BaseCounter that counts number of examples with feature set."""

  def add_input(self, accumulator, input_column):
    for feature_array in input_column.data.iterchunks():
      accumulator += len(feature_array) - feature_array.null_count
    return accumulator


GENERATE_STATS_TESTS = [
    {
        'testcase_name':
            'feature_whitelist',
        'examples': [{
            'a': np.array([1.0, 2.0], dtype=np.float32),
            'b': np.array([b'a', b'b', b'c', b'e'], dtype=np.object),
            'c': np.linspace(1, 500, 500, dtype=np.int64)
        }, {
            'a': np.array([3.0, 4.0, np.NaN, 5.0], dtype=np.float32),
            'b': np.array([b'a', b'c', b'd', b'a'], dtype=np.object),
            'c': np.linspace(501, 1250, 750, dtype=np.int64)
        }, {
            'a': np.array([1.0], dtype=np.float32),
            'b': np.array([b'a', b'b', b'c', b'd'], dtype=np.object),
            'c': np.linspace(1251, 3000, 1750, dtype=np.int64)
        }],
        'options':
            stats_options.StatsOptions(
                feature_whitelist=['b'],
                num_top_values=2,
                num_rank_histogram_buckets=3,
                num_values_histogram_buckets=3,
                num_histogram_buckets=3,
                num_quantiles_histogram_buckets=4,
                # Semantic domain stats are enabled by default for testing
                # to ensure they do not introduce regressions.
                enable_semantic_domain_stats=True),
        'expected_result_proto_text':
            """
                    datasets {
                      num_examples: 3
                      features {
                        name: "b"
                        type: STRING
                        string_stats {
                          common_stats {
                            num_non_missing: 3
                            min_num_values: 4
                            max_num_values: 4
                            avg_num_values: 4.0
                            tot_num_values: 12
                            num_values_histogram {
                              buckets {
                                low_value: 4.0
                                high_value: 4.0
                                sample_count: 1.0
                              }
                              buckets {
                                low_value: 4.0
                                high_value: 4.0
                                sample_count: 1.0
                              }
                              buckets {
                                low_value: 4.0
                                high_value: 4.0
                                sample_count: 1.0
                              }
                              type: QUANTILES
                            }
                          }
                          unique: 5
                          top_values {
                            value: "a"
                            frequency: 4.0
                          }
                          top_values {
                            value: "c"
                            frequency: 3.0
                          }
                          avg_length: 1.0
                          rank_histogram {
                            buckets {
                              low_rank: 0
                              high_rank: 0
                              label: "a"
                              sample_count: 4.0
                            }
                            buckets {
                              low_rank: 1
                              high_rank: 1
                              label: "c"
                              sample_count: 3.0
                            }
                            buckets {
                              low_rank: 2
                              high_rank: 2
                              label: "d"
                              sample_count: 2.0
                            }
                          }
                        }
                      }
                    }
                    """,
    },
    {
        'testcase_name':
            'schema',
        'examples': [{
            'a': np.array([1, 3, 5, 7])
        }, {
            'a': np.array([2, 4, 6, 8])
        }, {
            'a': np.array([0, 3, 6, 9])
        }],
        'options':
            stats_options.StatsOptions(
                num_top_values=2,
                num_rank_histogram_buckets=3,
                num_values_histogram_buckets=3,
                enable_semantic_domain_stats=True),
        'expected_result_proto_text':
            """
              datasets {
                num_examples: 3
                features {
                  name: "a"
                  type: INT
                  string_stats {
                    common_stats {
                      num_non_missing: 3
                      min_num_values: 4
                      max_num_values: 4
                      avg_num_values: 4.0
                      tot_num_values: 12
                      num_values_histogram {
                        buckets {
                          low_value: 4.0
                          high_value: 4.0
                          sample_count: 1.0
                        }
                        buckets {
                          low_value: 4.0
                          high_value: 4.0
                          sample_count: 1.0
                        }
                        buckets {
                          low_value: 4.0
                          high_value: 4.0
                          sample_count: 1.0
                        }
                        type: QUANTILES
                      }
                    }
                    unique: 10
                    top_values {
                      value: "6"
                      frequency: 2.0
                    }
                    top_values {
                      value: "3"
                      frequency: 2.0
                    }
                    avg_length: 1.0
                    rank_histogram {
                      buckets {
                        low_rank: 0
                        high_rank: 0
                        label: "6"
                        sample_count: 2.0
                      }
                      buckets {
                        low_rank: 1
                        high_rank: 1
                        label: "3"
                        sample_count: 2.0
                      }
                      buckets {
                        low_rank: 2
                        high_rank: 2
                        label: "9"
                        sample_count: 1.0
                      }
                    }
                  }
                }
              }
              """,
        'schema':
            text_format.Parse(
                """
              feature {
                name: "a"
                type: INT
                int_domain {
                  is_categorical: true
                }
              }
              """, schema_pb2.Schema())
    },
    {
        'testcase_name':
            'weight_feature',
        'examples': [
            {
                'a': np.array([1.0, 2.0], dtype=np.float32),
                'b': np.array([b'a', b'b', b'c', b'e'], dtype=np.object),
                'w': np.array([1.0], dtype=np.float32)
            },
            {
                'a': np.array([3.0, 4.0, 5.0, 6.0], dtype=np.float32),
                'b': np.array([b'd', b'e'], dtype=np.object),
                'w': np.array([2.0], dtype=np.float32)
            },
        ],
        'options':
            stats_options.StatsOptions(
                weight_feature='w',
                num_top_values=2,
                num_rank_histogram_buckets=2,
                num_values_histogram_buckets=2,
                num_histogram_buckets=2,
                num_quantiles_histogram_buckets=2,
                enable_semantic_domain_stats=True),
        'expected_result_proto_text':
            """
            datasets {
              num_examples: 2
              features {
                name: 'a'
                type: FLOAT
                num_stats {
                  common_stats {
                    num_non_missing: 2
                    num_missing: 0
                    min_num_values: 2
                    max_num_values: 4
                    avg_num_values: 3.0
                    tot_num_values: 6
                    num_values_histogram {
                      buckets {
                        low_value: 2.0
                        high_value: 4.0
                        sample_count: 1
                      }
                      buckets {
                        low_value: 4.0
                        high_value: 4.0
                        sample_count: 1
                      }
                      type: QUANTILES
                    }
                    weighted_common_stats {
                      num_non_missing: 3.0
                      num_missing: 0.0
                      avg_num_values: 3.3333333
                      tot_num_values: 10.0
                    }
                  }
                  mean: 3.5
                  std_dev: 1.7078251
                  num_zeros: 0
                  min: 1.0
                  max: 6.0
                  median: 4.0
                  histograms {
                    buckets {
                      low_value: 1.0
                      high_value: 3.5
                      sample_count: 2.985
                    }
                    buckets {
                      low_value: 3.5
                      high_value: 6.0
                      sample_count: 3.015
                    }
                    type: STANDARD
                  }
                  histograms {
                    buckets {
                      low_value: 1.0
                      high_value: 4.0
                      sample_count: 3.0
                    }
                    buckets {
                      low_value: 4.0
                      high_value: 6.0
                      sample_count: 3.0
                    }
                    type: QUANTILES
                  }
                  weighted_numeric_stats {
                    mean: 3.9
                    std_dev: 1.5779734
                    median: 4.0
                    histograms {
                      buckets {
                        low_value: 1.0
                        high_value: 3.5
                        sample_count: 3.975
                      }
                      buckets {
                        low_value: 3.5
                        high_value: 6.0
                        sample_count: 6.025
                      }
                    }
                    histograms {
                      buckets {
                        low_value: 1.0
                        high_value: 4.0
                        sample_count: 5.0
                      }
                      buckets {
                        low_value: 4.0
                        high_value: 6.0
                        sample_count: 5.0
                      }
                      type: QUANTILES
                    }
                  }
                }
              }
              features {
                name: 'b'
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 2
                    num_missing: 0
                    min_num_values: 2
                    max_num_values: 4
                    avg_num_values: 3.0
                    num_values_histogram {
                      buckets {
                        low_value: 2.0
                        high_value: 4.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 4.0
                        high_value: 4.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    weighted_common_stats {
                      num_non_missing: 3.0
                      num_missing: 0.0
                      avg_num_values: 2.6666667
                      tot_num_values: 8.0
                    }
                    tot_num_values: 6
                  }
                  avg_length: 1.0
                  unique: 5
                  top_values { value: 'e' frequency: 2.0 }
                  top_values { value: 'd' frequency: 1.0 }
                  rank_histogram {
                    buckets {
                      low_rank: 0
                      high_rank: 0
                      label: "e"
                      sample_count: 2.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "d"
                      sample_count: 1.0
                    }
                  }
                  weighted_string_stats {
                    top_values { value: 'e' frequency: 3.0 }
                    top_values { value: 'd' frequency: 2.0 }
                    rank_histogram {
                      buckets {
                        low_rank: 0
                        high_rank: 0
                        label: "e"
                        sample_count: 3.0
                      }
                      buckets {
                        low_rank: 1
                        high_rank: 1
                        label: "d"
                        sample_count: 2.0
                      }
                    }
                  }
                }
              }
            }
            """,
    },
    {
        'testcase_name':
            'custom_feature_generator',
        'examples': [
            {
                'a': np.array([b'doing'], dtype=np.object)
            },
            {
                'b': np.array([b'lala'], dtype=np.object)
            },
            {
                'a': np.array([b'din', b'don'], dtype=np.object),
                'b': np.array([b'lolo'], dtype=np.object),
            },
        ],
        'options':
            stats_options.StatsOptions(
                generators=[_ValueCounter(), _ExampleCounter()],
                num_top_values=4,
                num_rank_histogram_buckets=3,
                num_values_histogram_buckets=3,
                enable_semantic_domain_stats=True),
        'expected_result_proto_text':
            """
            datasets {
              num_examples: 3
              features {
                name: 'a'
                type: STRING
                custom_stats {
                  name: "_ValueCounter"
                  num: 3.0
                }
                custom_stats {
                  name: "_ExampleCounter"
                  num: 2.0
                }
                string_stats {
                  common_stats {
                    num_non_missing: 2
                    num_missing: 1
                    min_num_values: 1
                    max_num_values: 2
                    avg_num_values: 1.5
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 0.6666667
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 2.0
                        sample_count: 0.6666667
                      }
                      buckets {
                        low_value: 2.0
                        high_value: 2.0
                        sample_count: 0.6666667
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 3
                  }
                  unique: 3
                  top_values {
                    value: "don"
                    frequency: 1.0
                  }
                  top_values {
                    value: "doing"
                    frequency: 1.0
                  }
                  top_values {
                    value: "din"
                    frequency: 1.0
                  }
                  avg_length: 3.66666698456
                  rank_histogram {
                    buckets {
                      label: "don"
                      sample_count: 1.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "doing"
                      sample_count: 1.0
                    }
                    buckets {
                      low_rank: 2
                      high_rank: 2
                      label: "din"
                      sample_count: 1.0
                    }
                  }
                }
              }
              features {
                name: 'b'
                type: STRING
                custom_stats {
                  name: "_ValueCounter"
                  num: 2.0
                }
                custom_stats {
                  name: "_ExampleCounter"
                  num: 2.0
                }
                string_stats {
                  common_stats {
                    num_non_missing: 2
                    num_missing: 1
                    min_num_values: 1
                    max_num_values: 1
                    avg_num_values: 1.0
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 0.6666667
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 0.6666667
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 0.6666667
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 2
                  }
                  unique: 2
                  top_values {
                    value: "lolo"
                    frequency: 1.0
                  }
                  top_values {
                    value: "lala"
                    frequency: 1.0
                  }
                  avg_length: 4.0
                  rank_histogram {
                    buckets {
                      label: "lolo"
                      sample_count: 1.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "lala"
                      sample_count: 1.0
                    }
                  }
                }
              }
            }""",
    },
    {
        'testcase_name':
            'semantic_domains_enabled',
        # Generate 100 examples to pass threshold for semantic domains:
        # - Replicate an example passing checks 90 times
        # - Replicate an example not passing checks 10 times
        'examples': [
            {
                'text_feature':
                    np.array([b'This should be natural text'], dtype=np.object),
                # The png magic header, this should be considered an "image".
                'image_feature':
                    np.array([b'\211PNG\r\n\032\n'], dtype=np.object),
            },
        ] * 90 + [
            {
                'text_feature':
                    np.array([b'Thisshouldnotbenaturaltext'], dtype=np.object),
                # The png magic header, this should be considered an "image".
                'image_feature':
                    np.array([b'Thisisnotanimage'], dtype=np.object),
            },
        ] * 10,
        'options':
            stats_options.StatsOptions(
                num_top_values=4,
                num_rank_histogram_buckets=3,
                num_values_histogram_buckets=3,
                enable_semantic_domain_stats=True),
        'expected_result_proto_text':
            """
            datasets {
              num_examples: 100
              features {
                name: "text_feature"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 100
                    min_num_values: 1
                    max_num_values: 1
                    avg_num_values: 1.0
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 100
                  }
                  unique: 2
                  top_values {
                    value: "This should be natural text"
                    frequency: 90.0
                  }
                  top_values {
                    value: "Thisshouldnotbenaturaltext"
                    frequency: 10.0
                  }
                  avg_length: 26.8999996185
                  rank_histogram {
                    buckets {
                      label: "This should be natural text"
                      sample_count: 90.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "Thisshouldnotbenaturaltext"
                      sample_count: 10.0
                    }
                  }
                }
                custom_stats {
                  name: "domain_info"
                  str: "natural_language_domain {}"
                }
                custom_stats {
                  name: "natural_language_match_rate"
                  num: 0.9
                }
              }
              features {
                name: "image_feature"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 100
                    min_num_values: 1
                    max_num_values: 1
                    avg_num_values: 1.0
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 100
                  }
                  unique: 2
                  top_values {
                    value: "__BYTES_VALUE__"
                    frequency: 90.0
                  }
                  top_values {
                    value: "Thisisnotanimage"
                    frequency: 10.0
                  }
                  avg_length: 8.80000019073
                  rank_histogram {
                    buckets {
                      label: "__BYTES_VALUE__"
                      sample_count: 90.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "Thisisnotanimage"
                      sample_count: 10.0
                    }
                  }
                }
                custom_stats {
                  name: "domain_info"
                  str: "image_domain {}"
                }
                custom_stats {
                  name: "image_format_histogram"
                  rank_histogram {
                    buckets {
                      label: "UNKNOWN"
                      sample_count: 10.0
                    }
                    buckets {
                      label: "png"
                      sample_count: 90.0
                    }
                  }
                }
              }
            }""",
    },
    # Identical test with semantic_domains_enabled but with
    # options.enable_semantic_domain_stats=False
    {
        'testcase_name':
            'semantic_domains_disabled',
        'examples': [
            {
                'text_feature':
                    np.array([b'This should be natural text'], dtype=np.object),
                # The png magic header, this should be considered an "image".
                'image_feature':
                    np.array([b'\211PNG\r\n\032\n'], dtype=np.object)
            },
        ] * 90 + [
            {
                'text_feature':
                    np.array([b'Thisshouldnotbenaturaltext'], dtype=np.object),
                # The png magic header, this should be considered an "image".
                'image_feature':
                    np.array([b'Thisisnotanimage'], dtype=np.object)
            },
        ] * 10,
        'options':
            stats_options.StatsOptions(
                num_top_values=4,
                num_rank_histogram_buckets=3,
                num_values_histogram_buckets=3,
                enable_semantic_domain_stats=False),
        'expected_result_proto_text':
            """
            datasets {
              num_examples: 100
              features {
                name: "text_feature"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 100
                    min_num_values: 1
                    max_num_values: 1
                    avg_num_values: 1.0
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 100
                  }
                  unique: 2
                  top_values {
                    value: "This should be natural text"
                    frequency: 90.0
                  }
                  top_values {
                    value: "Thisshouldnotbenaturaltext"
                    frequency: 10.0
                  }
                  avg_length: 26.8999996185
                  rank_histogram {
                    buckets {
                      label: "This should be natural text"
                      sample_count: 90.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "Thisshouldnotbenaturaltext"
                      sample_count: 10.0
                    }
                  }
                }
              }
              features {
                name: "image_feature"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 100
                    min_num_values: 1
                    max_num_values: 1
                    avg_num_values: 1.0
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 33.3333333333
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 100
                  }
                  unique: 2
                  top_values {
                    value: "__BYTES_VALUE__"
                    frequency: 90.0
                  }
                  top_values {
                    value: "Thisisnotanimage"
                    frequency: 10.0
                  }
                  avg_length: 8.80000019073
                  rank_histogram {
                    buckets {
                      label: "__BYTES_VALUE__"
                      sample_count: 90.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "Thisisnotanimage"
                      sample_count: 10.0
                    }
                  }
                }
              }
            }""",
    },
]


SLICING_TESTS = [
    {
        'testcase_name':
            'feature_value_slicing',
        'examples': [
            {
                'a': np.array([1.0, 2.0], dtype=np.float32),
                'b': np.array([b'a'], dtype=np.object),
                'c': np.linspace(1, 500, 500, dtype=np.int64)
            },
            {
                'a': np.array([3.0, 4.0, np.NaN, 5.0], dtype=np.float32),
                'b': np.array([b'a', b'b'], dtype=np.object),
                'c': np.linspace(501, 1250, 750, dtype=np.int64)
            },
            {
                'a': np.array([1.0], dtype=np.float32),
                'b': np.array([b'b'], dtype=np.object),
                'c': np.linspace(1251, 3000, 1750, dtype=np.int64)
            }
        ],
        'options':
            stats_options.StatsOptions(
                slice_functions=[
                    slicing_util.get_feature_value_slicer({'b': None})
                ],
                num_top_values=2,
                num_rank_histogram_buckets=2,
                num_values_histogram_buckets=2,
                num_histogram_buckets=2,
                num_quantiles_histogram_buckets=2,
                enable_semantic_domain_stats=True),
        'expected_result_proto_text':
            """
            datasets {
              name: "All Examples"
              num_examples: 3
              features {
                name: "c"
                num_stats {
                  common_stats {
                    num_non_missing: 3
                    min_num_values: 500
                    max_num_values: 1750
                    avg_num_values: 1000.0
                    num_values_histogram {
                      buckets {
                        low_value: 500.0
                        high_value: 1750.0
                        sample_count: 1.5
                      }
                      buckets {
                        low_value: 1750.0
                        high_value: 1750.0
                        sample_count: 1.5
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 3000
                  }
                  mean: 1500.5
                  std_dev: 866.025355672
                  min: 1.0
                  median: 1503.0
                  max: 3000.0
                  histograms {
                    buckets {
                      low_value: 1.0
                      high_value: 1500.5
                      sample_count: 1497.65625
                    }
                    buckets {
                      low_value: 1500.5
                      high_value: 3000.0
                      sample_count: 1502.34375
                    }
                  }
                  histograms {
                    buckets {
                      low_value: 1.0
                      high_value: 1503.0
                      sample_count: 1500.0
                    }
                    buckets {
                      low_value: 1503.0
                      high_value: 3000.0
                      sample_count: 1500.0
                    }
                    type: QUANTILES
                  }
                }
              }
              features {
                name: "b"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 3
                    min_num_values: 1
                    max_num_values: 2
                    avg_num_values: 1.33333337307
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 1.0
                        sample_count: 1.5
                      }
                      buckets {
                        low_value: 1.0
                        high_value: 2.0
                        sample_count: 1.5
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 4
                  }
                  unique: 2
                  top_values {
                    value: "b"
                    frequency: 2.0
                  }
                  top_values {
                    value: "a"
                    frequency: 2.0
                  }
                  avg_length: 1.0
                  rank_histogram {
                    buckets {
                      label: "b"
                      sample_count: 2.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "a"
                      sample_count: 2.0
                    }
                  }
                }
              }
              features {
                name: "a"
                type: FLOAT
                num_stats {
                  common_stats {
                    num_non_missing: 3
                    min_num_values: 1
                    max_num_values: 4
                    avg_num_values: 2.33333325386
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 4.0
                        sample_count: 1.5
                      }
                      buckets {
                        low_value: 4.0
                        high_value: 4.0
                        sample_count: 1.5
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 7
                  }
                  mean: 2.66666666667
                  std_dev: 1.490711985
                  min: 1.0
                  median: 3.0
                  max: 5.0
                  histograms {
                    num_nan: 1
                    buckets {
                      low_value: 1.0
                      high_value: 3.0
                      sample_count: 3.0
                    }
                    buckets {
                      low_value: 3.0
                      high_value: 5.0
                      sample_count: 3.0
                    }
                  }
                  histograms {
                    num_nan: 1
                    buckets {
                      low_value: 1.0
                      high_value: 3.0
                      sample_count: 3.0
                    }
                    buckets {
                      low_value: 3.0
                      high_value: 5.0
                      sample_count: 3.0
                    }
                    type: QUANTILES
                  }
                }
              }
            }
            datasets {
              name: "b_a"
              num_examples: 2
              features {
                name: "c"
                num_stats {
                  common_stats {
                    num_non_missing: 2
                    min_num_values: 500
                    max_num_values: 750
                    avg_num_values: 625.0
                    num_values_histogram {
                      buckets {
                        low_value: 500.0
                        high_value: 750.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 750.0
                        high_value: 750.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 1250
                  }
                  mean: 625.5
                  std_dev: 360.843802773
                  min: 1.0
                  median: 627.0
                  max: 1250.0
                  histograms {
                    buckets {
                      low_value: 1.0
                      high_value: 625.5
                      sample_count: 623.828125
                    }
                    buckets {
                      low_value: 625.5
                      high_value: 1250.0
                      sample_count: 626.171875
                    }
                  }
                  histograms {
                    buckets {
                      low_value: 1.0
                      high_value: 627.0
                      sample_count: 625.0
                    }
                    buckets {
                      low_value: 627.0
                      high_value: 1250.0
                      sample_count: 625.0
                    }
                    type: QUANTILES
                  }
                }
              }
              features {
                name: "b"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 2
                    min_num_values: 1
                    max_num_values: 2
                    avg_num_values: 1.5
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 2.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 2.0
                        high_value: 2.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 3
                  }
                  unique: 2
                  top_values {
                    value: "a"
                    frequency: 2.0
                  }
                  top_values {
                    value: "b"
                    frequency: 1.0
                  }
                  avg_length: 1.0
                  rank_histogram {
                    buckets {
                      label: "a"
                      sample_count: 2.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "b"
                      sample_count: 1.0
                    }
                  }
                }
              }
              features {
                name: "a"
                type: FLOAT
                num_stats {
                  common_stats {
                    num_non_missing: 2
                    min_num_values: 2
                    max_num_values: 4
                    avg_num_values: 3.0
                    num_values_histogram {
                      buckets {
                        low_value: 2.0
                        high_value: 4.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 4.0
                        high_value: 4.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 6
                  }
                  mean: 3.0
                  std_dev: 1.41421356237
                  min: 1.0
                  median: 3.0
                  max: 5.0
                  histograms {
                    num_nan: 1
                    buckets {
                      low_value: 1.0
                      high_value: 3.0
                      sample_count: 2.0
                    }
                    buckets {
                      low_value: 3.0
                      high_value: 5.0
                      sample_count: 3.0
                    }
                  }
                  histograms {
                    num_nan: 1
                    buckets {
                      low_value: 1.0
                      high_value: 3.0
                      sample_count: 2.5
                    }
                    buckets {
                      low_value: 3.0
                      high_value: 5.0
                      sample_count: 2.5
                    }
                    type: QUANTILES
                  }
                }
              }
            }
            datasets {
              name: "b_b"
              num_examples: 2
              features {
                name: "c"
                num_stats {
                  common_stats {
                    num_non_missing: 2
                    min_num_values: 750
                    max_num_values: 1750
                    avg_num_values: 1250.0
                    num_values_histogram {
                      buckets {
                        low_value: 750.0
                        high_value: 1750.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 1750.0
                        high_value: 1750.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 2500
                  }
                  mean: 1750.5
                  std_dev: 721.687778752
                  min: 501.0
                  median: 1747.0
                  max: 3000.0
                  histograms {
                    buckets {
                      low_value: 501.0
                      high_value: 1750.5
                      sample_count: 1252.734375
                    }
                    buckets {
                      low_value: 1750.5
                      high_value: 3000.0
                      sample_count: 1247.265625
                    }
                  }
                  histograms {
                    buckets {
                      low_value: 501.0
                      high_value: 1747.0
                      sample_count: 1250.0
                    }
                    buckets {
                      low_value: 1747.0
                      high_value: 3000.0
                      sample_count: 1250.0
                    }
                    type: QUANTILES
                  }
                }
              }
              features {
                name: "b"
                type: STRING
                string_stats {
                  common_stats {
                    num_non_missing: 2
                    min_num_values: 1
                    max_num_values: 2
                    avg_num_values: 1.5
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 2.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 2.0
                        high_value: 2.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 3
                  }
                  unique: 2
                  top_values {
                    value: "b"
                    frequency: 2.0
                  }
                  top_values {
                    value: "a"
                    frequency: 1.0
                  }
                  avg_length: 1.0
                  rank_histogram {
                    buckets {
                      label: "b"
                      sample_count: 2.0
                    }
                    buckets {
                      low_rank: 1
                      high_rank: 1
                      label: "a"
                      sample_count: 1.0
                    }
                  }
                }
              }
              features {
                name: "a"
                type: FLOAT
                num_stats {
                  common_stats {
                    num_non_missing: 2
                    min_num_values: 1
                    max_num_values: 4
                    avg_num_values: 2.5
                    num_values_histogram {
                      buckets {
                        low_value: 1.0
                        high_value: 4.0
                        sample_count: 1.0
                      }
                      buckets {
                        low_value: 4.0
                        high_value: 4.0
                        sample_count: 1.0
                      }
                      type: QUANTILES
                    }
                    tot_num_values: 5
                  }
                  mean: 3.25
                  std_dev: 1.47901994577
                  min: 1.0
                  median: 4.0
                  max: 5.0
                  histograms {
                    num_nan: 1
                    buckets {
                      low_value: 1.0
                      high_value: 3.0
                      sample_count: 1.0
                    }
                    buckets {
                      low_value: 3.0
                      high_value: 5.0
                      sample_count: 3.0
                    }
                  }
                  histograms {
                    num_nan: 1
                    buckets {
                      low_value: 1.0
                      high_value: 4.0
                      sample_count: 2.0
                    }
                    buckets {
                      low_value: 4.0
                      high_value: 5.0
                      sample_count: 2.0
                    }
                    type: QUANTILES
                  }
                }
              }
            }
            """,
    },
]


class StatsImplTest(parameterized.TestCase):

  @parameterized.named_parameters(*(GENERATE_STATS_TESTS + SLICING_TESTS))
  def test_stats_impl(self,
                      examples,
                      options,
                      expected_result_proto_text,
                      schema=None):
    expected_result = text_format.Parse(
        expected_result_proto_text,
        statistics_pb2.DatasetFeatureStatisticsList())
    if schema is not None:
      options.schema = schema
    with beam.Pipeline() as p:
      result = (
          p | beam.Create(examples)
          | stats_impl.GenerateStatisticsImpl(options))
      util.assert_that(
          result,
          test_util.make_dataset_feature_stats_list_proto_equal_fn(
              self, expected_result))

  def test_generate_sliced_statistics_impl_without_slice_fns(self):
    examples = [
        ('test_slice', {
            'b': np.array([], dtype=np.float32),
        }),
        ('test_slice', {
            'b': np.array([], dtype=np.float32),
        }),
    ]
    # No slice functions are specified in options.
    options = stats_options.StatsOptions(
        num_top_values=2,
        num_rank_histogram_buckets=2,
        num_values_histogram_buckets=2)
    expected_result_without_slice_key = text_format.Parse(
        """
        datasets {
          num_examples: 2
          features {
            name: "b"
            type: FLOAT
            num_stats {
              common_stats {
                num_non_missing: 2
                num_values_histogram {
                  buckets {
                    sample_count: 1.0
                  }
                  buckets {
                    sample_count: 1.0
                  }
                  type: QUANTILES
                }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())
    expected_result_with_slice_key = text_format.Parse(
        """
        datasets {
          name: "test_slice"
          num_examples: 2
          features {
            name: "b"
            type: FLOAT
            num_stats {
              common_stats {
                num_non_missing: 2
                num_values_histogram {
                  buckets {
                    sample_count: 1.0
                  }
                  buckets {
                    sample_count: 1.0
                  }
                  type: QUANTILES
                }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())
    with beam.Pipeline() as p:
      result = (
          p | beam.Create(examples)
          | stats_impl.GenerateSlicedStatisticsImpl(options=options))
      # GenerateSlicedStatisticsImpl() does not add slice keys to the result
      # because is_slicing_enabled is not set to True (and no slice functions
      # are provided via the stats options).
      util.assert_that(
          result,
          test_util.make_dataset_feature_stats_list_proto_equal_fn(
              self, expected_result_without_slice_key))

    with beam.Pipeline() as p:
      result = (
          p | beam.Create(examples)
          | stats_impl.GenerateSlicedStatisticsImpl(
              options=options, is_slicing_enabled=True))
      # GenerateSlicedStatisticsImpl() adds slice keys to the result because
      # is_slicing_enabled is set to True.
      util.assert_that(
          result,
          test_util.make_dataset_feature_stats_list_proto_equal_fn(
              self, expected_result_with_slice_key))

  @parameterized.named_parameters(*GENERATE_STATS_TESTS)
  def test_generate_statistics_in_memory(
      self, examples, options, expected_result_proto_text, schema=None):
    expected_result = text_format.Parse(
        expected_result_proto_text,
        statistics_pb2.DatasetFeatureStatisticsList())
    if schema is not None:
      options.schema = schema
    result = stats_impl.generate_statistics_in_memory(
        examples, options)
    # generate_statistics_in_memory does not deterministically
    # order multiple features within a DatasetFeatureStatistics proto. So, we
    # cannot use compare.assertProtoEqual (which requires the same ordering of
    # repeated fields) here.
    test_util.assert_dataset_feature_stats_proto_equal(
        self, result.datasets[0], expected_result.datasets[0])

  def test_stats_impl_custom_generators(self):

    # Dummy PTransform that returns two DatasetFeatureStatistics protos.
    class CustomPTransform(beam.PTransform):

      def expand(self, pcoll):
        stats_proto1 = statistics_pb2.DatasetFeatureStatistics()
        proto1_feat = stats_proto1.features.add()
        proto1_feat.name = 'a'
        custom_stat1 = proto1_feat.custom_stats.add()
        custom_stat1.name = 'my_stat_a'
        custom_stat1.str = 'my_val_a'

        stats_proto2 = statistics_pb2.DatasetFeatureStatistics()
        proto2_feat = stats_proto2.features.add()
        proto2_feat.name = 'b'
        custom_stat2 = proto2_feat.custom_stats.add()
        custom_stat2.name = 'my_stat_b'
        custom_stat2.str = 'my_val_b'
        return [(None, stats_proto1),
                (None, stats_proto2)]

    examples = [{'a': np.array([], dtype=np.int64),
                 'b': np.array([], dtype=np.int64)}]
    expected_result = text_format.Parse("""
    datasets {
      num_examples: 1
      features {
        name: 'a'
        type: INT
        num_stats {
          common_stats {
            num_non_missing: 1
            num_missing: 0
            tot_num_values: 0
            num_values_histogram {
              buckets {
                low_value: 0
                high_value: 0
                sample_count: 0.5
              }
              buckets {
                low_value: 0
                high_value: 0
                sample_count: 0.5
              }
              type: QUANTILES
            }
          }
        }
        custom_stats {
          name: 'my_stat_a'
          str: 'my_val_a'
        }
      }
      features {
        name: 'b'
        type: INT
        num_stats {
          common_stats {
            num_non_missing: 1
            num_missing: 0
            tot_num_values: 0
            num_values_histogram {
              buckets {
                low_value: 0
                high_value: 0
                sample_count: 0.5
              }
              buckets {
                low_value: 0
                high_value: 0
                sample_count: 0.5
              }
              type: QUANTILES
            }
          }
        }
        custom_stats {
          name: 'my_stat_b'
          str: 'my_val_b'
        }
      }
    }
    """, statistics_pb2.DatasetFeatureStatisticsList())

    # Create a transform stats generator.
    transform_stats_gen = stats_generator.TransformStatsGenerator(
        name='CustomStatsGenerator',
        ptransform=CustomPTransform())
    with beam.Pipeline() as p:
      options = stats_options.StatsOptions(
          generators=[transform_stats_gen],
          num_values_histogram_buckets=2,
          enable_semantic_domain_stats=True)
      result = (p | beam.Create(examples) |
                stats_impl.GenerateStatisticsImpl(options))
      util.assert_that(
          result,
          test_util.make_dataset_feature_stats_list_proto_equal_fn(
              self, expected_result))

  def test_generate_statistics_in_memory_empty_examples(self):
    examples = []
    expected_result = text_format.Parse(
        """
        datasets {
          num_examples: 0
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    result = stats_impl.generate_statistics_in_memory(examples)
    compare.assertProtoEqual(
        self, result, expected_result, normalize_numbers=True)

  def test_generate_statistics_in_memory_valid_custom_generator(
      self):

    # CombinerStatsGenerator that returns a DatasetFeatureStatistic proto with
    # custom stat.
    class CustomCombinerStatsGenerator(stats_generator.CombinerStatsGenerator):

      def create_accumulator(self):
        return 0

      def add_input(self, accumulator,
                    input_batch):
        return 0

      def merge_accumulators(self, accumulators):
        return 0

      def extract_output(
          self, accumulator):
        stats_proto = statistics_pb2.DatasetFeatureStatistics()
        proto_feature = stats_proto.features.add()
        proto_feature.name = 'a'
        custom_stat = proto_feature.custom_stats.add()
        custom_stat.name = 'custom_stat'
        custom_stat.str = 'custom_stat_value'
        return stats_proto

    examples = [
        {'a': np.array([b'xyz', b'qwe'], dtype=np.object)},
        {'a': np.array([b'qwe'], dtype=np.object)},
        {'a': np.array([b'qwe'], dtype=np.object)},
    ]

    expected_result = text_format.Parse(
        """
        datasets {
          num_examples: 3
          features {
            name: 'a'
            type: STRING
            custom_stats {
              name: 'custom_stat'
              str: 'custom_stat_value'
            }
            string_stats {
              avg_length: 3
              unique: 2
              common_stats {
                num_non_missing: 3
                min_num_values: 1
                max_num_values: 2
                avg_num_values: 1.333333
                tot_num_values: 4
                num_values_histogram {
                  buckets {
                    low_value: 1.0
                    high_value: 1.0
                    sample_count: 1.0
                  }
                  buckets {
                    low_value: 1.0
                    high_value: 2.0
                    sample_count: 1.0
                  }
                  buckets {
                    low_value: 2.0
                    high_value: 2.0
                    sample_count: 1.0
                  }
                  type: QUANTILES
                }
              }
              top_values {
                value: 'qwe'
                frequency: 3
              }
              top_values {
                value: 'xyz'
                frequency: 1
              }
              rank_histogram {
                buckets {
                  low_rank: 0
                  high_rank: 0
                  label: "qwe"
                  sample_count: 3.0
                }
                buckets {
                  low_rank: 1
                  high_rank: 1
                  label: "xyz"
                  sample_count: 1.0
                }
              }
            }
          }
        }""", statistics_pb2.DatasetFeatureStatisticsList())

    options = stats_options.StatsOptions(
        generators=[CustomCombinerStatsGenerator('CustomStatsGenerator')],
        num_top_values=4,
        num_rank_histogram_buckets=3,
        num_values_histogram_buckets=3,
        enable_semantic_domain_stats=True)
    result = stats_impl.generate_statistics_in_memory(
        examples, options)
    compare.assertProtoEqual(
        self, result, expected_result, normalize_numbers=True)

  def test_generate_statistics_in_memory_invalid_custom_generator(
      self):

    # Dummy PTransform that does nothing.
    class CustomPTransform(beam.PTransform):

      def expand(self, pcoll):
        pass

    examples = [{'a': np.array([1.0])}]
    custom_generator = stats_generator.TransformStatsGenerator(
        name='CustomStatsGenerator', ptransform=CustomPTransform())
    options = stats_options.StatsOptions(
        generators=[custom_generator], enable_semantic_domain_stats=True)
    with self.assertRaisesRegexp(
        TypeError, 'Statistics generator.* found object of type '
        'TransformStatsGenerator.'):
      stats_impl.generate_statistics_in_memory(examples, options)

  def test_merge_dataset_feature_stats_protos(self):
    proto1 = text_format.Parse(
        """
        num_examples: 7
        features: {
          name: 'feature1'
          type: STRING
          string_stats: {
            common_stats: {
              num_missing: 3
              num_non_missing: 4
              min_num_values: 1
              max_num_values: 1
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatistics())

    proto2 = text_format.Parse(
        """
        features: {
          name: 'feature1'
          type: STRING
          string_stats: {
            unique: 3
          }
        }
        """, statistics_pb2.DatasetFeatureStatistics())

    expected = text_format.Parse(
        """
        num_examples: 7
        features: {
          name: 'feature1'
          type: STRING
          string_stats: {
            common_stats: {
              num_missing: 3
              num_non_missing: 4
              min_num_values: 1
              max_num_values: 1
            }
            unique: 3
          }
        }
        """, statistics_pb2.DatasetFeatureStatistics())

    actual = stats_impl._merge_dataset_feature_stats_protos([proto1, proto2])
    self.assertEqual(actual, expected)

  def test_merge_dataset_feature_stats_protos_single_proto(self):
    proto1 = text_format.Parse(
        """
        num_examples: 7
        features: {
          name: 'feature1'
          type: STRING
          string_stats: {
            common_stats: {
              num_missing: 3
              num_non_missing: 4
              min_num_values: 1
              max_num_values: 1
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatistics())

    expected = text_format.Parse(
        """
        num_examples: 7
        features: {
          name: 'feature1'
          type: STRING
          string_stats: {
            common_stats: {
              num_missing: 3
              num_non_missing: 4
              min_num_values: 1
              max_num_values: 1
            }
          }
        }
        """, statistics_pb2.DatasetFeatureStatistics())

    actual = stats_impl._merge_dataset_feature_stats_protos([proto1])
    self.assertEqual(actual, expected)

  def test_merge_dataset_feature_stats_protos_empty(self):
    self.assertEqual(stats_impl._merge_dataset_feature_stats_protos([]),
                     statistics_pb2.DatasetFeatureStatistics())

  def test_make_dataset_feature_statistics_list_proto(self):
    input_proto = text_format.Parse(
        """
        num_examples: 7
        features: {
          name: 'feature1'
          type: STRING
        }
        """, statistics_pb2.DatasetFeatureStatistics())

    expected = text_format.Parse(
        """
        datasets {
          num_examples: 7
          features: {
            name: 'feature1'
            type: STRING
          }
        }
        """, statistics_pb2.DatasetFeatureStatisticsList())

    self.assertEqual(
        stats_impl._make_dataset_feature_statistics_list_proto([input_proto]),
        expected)

  def test_tfdv_telemetry(self):
    examples = [
        {
            'a': np.array([1.0, 2.0], dtype=np.float32),
            'b': np.array([b'a', b'b', b'c', b'e'], dtype=np.object),
            'c': None
        },
        {
            'a': np.array([3.0, 4.0, np.NaN, 5.0], dtype=np.float32),
            'b': np.array([b'd', b'e', b'f'], dtype=np.object),
            'c': None
        },
        {
            'a': None,
            'b': np.array([b'a', b'b', b'c'], dtype=np.object),
            'c': np.array([10, 20, 30], dtype=np.int64)
        },
        {
            'a': np.array([5.0], dtype=np.float32),
            'b': np.array([b'd', b'e', b'f'], dtype=np.object),
            'c': np.array([1], dtype=np.int64)
        }
    ]

    p = beam.Pipeline()
    _ = (p
         | 'CreateBatches' >> beam.Create(examples)
         | 'BasicStatsCombiner' >> beam.CombineGlobally(
             stats_impl._CombinerStatsGeneratorsCombineFn(
                 [basic_stats_generator.BasicStatsGenerator()])))

    runner = p.run()
    runner.wait_until_finish()
    result_metrics = runner.metrics()

    # TODO(b/125474748): Add all the counters.
    expected_result = {
        'num_instances': 4,
        'num_missing_feature_values': 3,
        'num_int_feature_values': 2,
        'int_feature_values_min_count': 1,
        'int_feature_values_max_count': 3,
        'int_feature_values_mean_count': 2,
        'num_float_feature_values': 3,
        'float_feature_values_min_count': 1,
        'float_feature_values_max_count': 4,
        'float_feature_values_mean_count': 2,
        'num_string_feature_values': 4,
        'string_feature_values_min_count': 3,
        'string_feature_values_max_count': 4,
        'string_feature_values_mean_count': 3,
    }

    # Check each counter.
    for counter_name in expected_result:
      actual_counter = result_metrics.query(
          beam.metrics.metric.MetricsFilter().with_name(counter_name)
          )['counters']
      self.assertLen(actual_counter, 1)
      self.assertEqual(actual_counter[0].committed,
                       expected_result[counter_name])

  def test_filter_features(self):
    input_batch = {'a': np.array([]), 'b': np.array([]), 'c': np.array([])}
    actual = stats_impl._filter_features(input_batch, ['a', 'c'])
    expected = {'a': np.array([]), 'c': np.array([])}
    self.assertEqual(set(actual.keys()), set(expected.keys()))

  def test_filter_features_empty(self):
    input_batch = {'a': np.array([])}
    actual = stats_impl._filter_features(input_batch, [])
    expected = {}
    self.assertEqual(set(actual.keys()), set(expected.keys()))


if __name__ == '__main__':
  absltest.main()
