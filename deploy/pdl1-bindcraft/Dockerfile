# Governed PDL1 image layout. The repository development Dockerfile is not
# this image. BASE_IMAGE is repository@sha256 of the image built from
# Dockerfile.base after that image has been pushed. A local image ID is
# not that reference. A stock CUDA tag has no BindCraft conda environment
# and cannot pass assert_intended_runtime_layout. No base digest, CUDA
# version, ColabDesign commit, PyRosetta wheel, or AlphaFold parameter
# digest is selected here. A missing pin fails this build before BindCraft
# is checked out.

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG COLABDESIGN_COMMIT
ARG PYROSETTA_WHEEL_FILENAME
ARG PYROSETTA_WHEEL_SHA256
ARG ALPHAFOLD_PARAMS_SHA256
ARG HOST_NVIDIA_DRIVER
ARG CUDA_VERSION
ARG BINDCRAFT_COMMIT=efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d

RUN test -n "$COLABDESIGN_COMMIT" \
    && test -n "$PYROSETTA_WHEEL_FILENAME" \
    && test -n "$PYROSETTA_WHEEL_SHA256" \
    && test -n "$ALPHAFOLD_PARAMS_SHA256" \
    && test -n "$HOST_NVIDIA_DRIVER" \
    && test -n "$CUDA_VERSION" \
    && test "$BINDCRAFT_COMMIT" = "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d" \
    || { echo image_pins_incomplete >&2; exit 1; }

COPY src/cellertz /opt/cellertz-src/cellertz
ENV PYTHONPATH=/opt/cellertz-src
ENV PATH="/opt/conda/envs/BindCraft/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# CPU-only probe of the published base stack. This does not run bindcraft.py.
# JAX_PLATFORMS applies to this RUN only. The absolute interpreter is the
# BindCraft conda environment from the base recipe.
RUN JAX_PLATFORMS=cpu CUDA_VISIBLE_DEVICES="" \
    /opt/conda/envs/BindCraft/bin/python -c "from cellertz.application.pdl1_image_readiness import assert_base_environment, assert_intended_runtime_layout; assert_base_environment(); assert_intended_runtime_layout()" \
    || { echo base_environment_incomplete >&2; exit 1; }

RUN git init /opt/BindCraft \
    && git -C /opt/BindCraft remote add origin https://github.com/martinpacesa/BindCraft.git \
    && git -C /opt/BindCraft fetch --depth 1 origin "$BINDCRAFT_COMMIT" \
    && git -C /opt/BindCraft checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/BindCraft rev-parse HEAD)" = "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d" \
    && chmod 0755 /opt/BindCraft/functions/dssp /opt/BindCraft/functions/DAlphaBall.gcc \
    && JAX_PLATFORMS=cpu /opt/conda/envs/BindCraft/bin/python -c "from pathlib import Path; from cellertz.application.pdl1_image_readiness import assert_bindcraft_files, assert_dalphaball_libraries; assert_bindcraft_files(Path('/opt/BindCraft')); assert_dalphaball_libraries(Path('/opt/BindCraft/functions/DAlphaBall.gcc'))"

COPY deploy/pdl1-bindcraft/vendor/ /tmp/vendor/
RUN /opt/conda/envs/BindCraft/bin/python -c "from cellertz.application.pdl1_image_readiness import assert_wheel_filename; assert_wheel_filename('$PYROSETTA_WHEEL_FILENAME')" \
    && test -f "/tmp/vendor/${PYROSETTA_WHEEL_FILENAME}" \
    && test -f /tmp/vendor/alphafold_params_2022-12-06.tar \
    && echo "$PYROSETTA_WHEEL_SHA256  /tmp/vendor/${PYROSETTA_WHEEL_FILENAME}" | sha256sum -c - \
    && echo "$ALPHAFOLD_PARAMS_SHA256  /tmp/vendor/alphafold_params_2022-12-06.tar" | sha256sum -c - \
    && /opt/conda/envs/BindCraft/bin/python -m pip install --no-deps "/tmp/vendor/${PYROSETTA_WHEEL_FILENAME}" \
    && /opt/conda/envs/BindCraft/bin/python -m pip install --no-deps \
        "git+https://github.com/sokrypton/ColabDesign.git@${COLABDESIGN_COMMIT}" \
    && mkdir -p /opt/BindCraft/params \
    && tar -xf /tmp/vendor/alphafold_params_2022-12-06.tar -C /opt/BindCraft/params \
    && rm -rf /tmp/vendor \
    && JAX_PLATFORMS=cpu /opt/conda/envs/BindCraft/bin/python -c "from cellertz.application.pdl1_image_readiness import assert_post_install; assert_post_install()"

COPY deploy/pdl1-bindcraft/pdl1-entrypoint /usr/local/bin/pdl1-entrypoint
RUN chmod 0755 /usr/local/bin/pdl1-entrypoint

WORKDIR /opt/BindCraft
ENTRYPOINT ["pdl1-entrypoint"]
