# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---

# %% [markdown]
# # 4. Video Converters
#
# Video converters enable transformations involving video files, particularly adding images to videos.
#
# ## Overview
#
# This notebook covers:
#
# - **[Image to Video](#image-to-video)**: Add images to video files

# %% [markdown]
# <a id="image-to-video"></a>
# ## Image to Video
#
# ### AddImageVideoConverter
#
# The `AddImageVideoConverter` adds an image overlay to a video file.
# To use this converter you'll need to install opencv which can be done with `pip install pyrit[opencv]`

# %%
import pathlib

from pyrit.prompt_converter import AddImageVideoConverter
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

input_video = str(pathlib.Path(".") / ".." / ".." / ".." / "assets" / "sample_video.mp4")
input_image = str(pathlib.Path(".") / ".." / ".." / ".." / "assets" / "pyrit_architecture.png")

video = AddImageVideoConverter(video_path=input_video)
converted_vid = await video.convert_async(prompt=input_image, input_type="image_path")  # type: ignore
print(converted_vid)
