import numpy as np
import scipy.signal as signal
from scipy.linalg import solve_toeplitz
from passiveRadar.signal_utils import xcorr, shift, frequency_shift
import pyfftw

wisdom_fft = (b'(fftw-3.3.5 fftw_wisdom #x3c273403 #x192df114 #x4d08727c #xe98e9b9d\n  (fftw_codelet_t2fv_32_avx 0 #x1040 #x1040 #x0 #x5ccd11b9 #x43ed8830 #x7233cc84 #xe3e53a93)\n  (fftw_codelet_n2fv_16_avx 0 #x1040 #x1040 #x0 #xceb78709 #x256b5ce3 #x1e7977c0 #x7e59d943)\n)\n', b'(fftw-3.3.5 fftwf_wisdom #x706526c0 #x2f8b6c85 #x8cd1bb1a #x7c96e03d\n)\n', b'(fftw-3.3.5 fftwl_wisdom #x0821b5c7 #xa4c07d5a #x21b58211 #xebe513ab\n)\n')
pyfftw.import_wisdom(wisdom_fft)


def fast_xambg(ref, srv, nlag, nf):
    ''' Fast Cross-Ambiguity Fuction
    
    Parameters:
        ref, srv: input vectors for cross-ambiguity function
        nlag: number of lag bins to compute
        nfft: number of doppler bins to compute (should be power of 2)
    Returns:
        xambg: (nf, nlag+1, 1) matrix containing cross-ambiguity surface
        third dimension added for easy stacking in dask

    '''
    if ref.shape != srv.shape:
        raise ValueError('Input vectors must have the same length')

    ndecim = int(ref.shape[0]/nf)
    xambg = np.zeros((nf, nlag+1, 1), dtype=np.complex64)
    s2c = np.conj(srv)

    # precompute FIR filter for decimation
    dtaps = signal.firwin(10*ndecim + 1, 1. / ndecim, window='flattop')
    dfilt = signal.dlti(dtaps, 1)
    
    for k, lag in enumerate(np.arange(-nlag, 1)):
        sd = np.roll(s2c, lag)*ref
        sd = signal.decimate(sd, ndecim, ftype=dfilt)
        xambg[:, k, 0] = np.fft.fftshift(np.fft.fft(sd, nf))

    return xambg

def fast_xambg_b(ref, srv, nlag, nf):
    ''' Fast Cross-Ambiguity Fuction
    
    Parameters:
        ref, srv: input vectors for cross-ambiguity function
        nlag: number of lag bins to compute
        nfft: number of doppler bins to compute (should be power of 2)
    Returns:
        xambg: (nf, nlag+1, 1) matrix containing cross-ambiguity surface
        third dimension added for easy stacking in dask

    '''
    if ref.shape != srv.shape:
        raise ValueError('Input vectors must have the same length')

    ndecim = int(ref.shape[0]/nf)
    xambg = np.zeros((nf, nlag+1, 1), dtype=np.complex64)
    s2c = np.conj(srv)

    # precompute FIR filter for decimation
    # dtaps = signal.firwin(10*ndecim + 1, 1. / ndecim, window='flattop')
    dtaps = np.ones((ndecim,))
    dfilt = signal.dlti(dtaps, 1)
    
    for k, lag in enumerate(np.arange(-nlag, 1)):
        sd = np.roll(s2c, lag)*ref
        xambg[:, k, 0] = signal.decimate(sd, ndecim, ftype=dfilt)
        # sd = signal.decimate(sd, ndecim, ftype=dfilt)
        # xambg[:, k, 0] = np.fft.fftshift(np.fft.fft(sd, nf))
    
    xambg = np.fft.fftshift(np.fft.fft(xambg, axis=0), axes=0)
    return xambg

def fast_xambg_fftw(ref, srv, nlag, nf):
    ''' Fast Cross-Ambiguity Fuction using the pyFFTW library
    
    Parameters:
        ref, srv: input vectors for cross-ambiguity function
        nlag: number of lag bins to compute
        nfft: number of doppler bins to compute (should be power of 2)
    Returns:
        xambg: (nf, nlag+1, 1) matrix containing cross-ambiguity surface
        third dimension added for easy stacking in dask

    '''
    if ref.shape != srv.shape:
        raise ValueError('Input vectors must have the same length')

    ndecim = int(ref.shape[0]/nf)
    xambg = np.zeros((nf, nlag+1, 1), dtype=np.complex64)
    s2c = np.conj(srv)

    # decimate in two stages: first by ndecim/16 then by 16
    nc1 = int(ndecim / 16)

    # precompute FIR taps for decimation
    # ftaps = signal.firwin(10*nc1 + 1, 1. / nc1, window='flattop')
    # dtaps = signal.firwin(10*16  + 1, 1. / 16,  window='flattop')

    b00, a00 = signal.cheby1(8, 0.01, 1./nc1)
    b01, a01 = signal.cheby1(8, 0.01, 1./16)

    filt00 = signal.dlti(b00, a00)
    filt01 = signal.dlti(b01, a01)

    # get ready to do some FFTs 
    tmp_in   = pyfftw.empty_aligned(nf, dtype='complex128')
    tmp_out  = pyfftw.empty_aligned(nf, dtype='complex128')
    fft_obj  = pyfftw.FFTW(tmp_in, tmp_out, flags=["FFTW_WISDOM_ONLY"])

    for k, lag in enumerate(np.arange(-nlag, 1)):
        sd = signal.decimate(np.roll(s2c, lag)*ref, nc1, ftype=filt00)
        tmp_in[:] = signal.decimate(sd, 16, ftype=filt01)
        tmp_out   = fft_obj()
        xambg[:, k, 0] = np.fft.fftshift(tmp_out)

    return xambg

def direct_xambg(ref, srv, fs, fd, df, nlag):
    '''Create a range-doppler map from two signals
    
    input parameters:
    ref, srv:       input signals
    fs:             input sample rate (samples/sec)
    fd:             number of Doppler bins to compute
    df:             doppler resolution (Hz)
    nlag:           maximum lag between signals (samples)
    '''
    if ref.shape != srv.shape:
        raise ValueError('Input vectors must have the same length')

    omega_d = 2*np.pi*np.arange(-1*fd, fd-df, df)/fs
    nf = omega_d.shape[0]
    t = np.arange(ref.shape[0])  
    xambg = np.zeros((nf, nlag+1, 1), dtype=np.complex64)
    
    for k in range(nf):
        srv_shift = np.exp(1j*omega_d[k]*t)*srv
        xambg[k,:, 0] = xcorr(ref, srv_shift,0,nlag)     
        
    return xambg


def LS_Filter(ref, srv, nlag, reg=1, return_filter=False):
    '''Least squares clutter removal for passive radar. Computes filter taps
    using the direct matrix inversion method.  
    
    Parameters:
    ref: reference signal
    srv: surveillance signal
    nlag: filter length in samples
    reg: L2 regularization parameter for matrix inversion (default 1)
    return_filter: (bool) option to return filter taps as well as cleaned signal
    
    Returns:
    y: surveillance signal with clutter removed
    w: (optional) least squares filter taps

    '''
    if ref.shape != srv.shape:
        raise ValueError('Input vectors must have the same length')
    A = np.zeros((ref.shape[0], nlag+10), dtype=np.complex64)
    lags = np.arange(-10, nlag)
    
    # compute the data matrix
    for k in range(lags.shape[0]):
        A[:, k] = np.roll(ref, lags[k])
    
    # compute the autocorrelation matrix of ref
    ATA = A.conj().T @ A

    # create the Tikhonov regularization matrix
    K = np.eye(ATA.shape[0], dtype=np.complex64)

    # solve the least squares problem
    w = np.linalg.solve(ATA + K*reg, A.conj().T @ srv)

    # direct but slightly slower implementation:
    # w = np.inv(ATA + K*reg) @ A.conj().T @ srv

    if return_filter:
        return srv - A @ w, w
    else:
        return srv - A @ w

def LS_Filter_SVD(ref, srv, nlag, return_filter = False):
    '''Least squares clutter removal for passive radar. Computes filter taps
    using the singular value decomposition. Slower than the direct matrix
    inversion, but guaranteed to be stable.
    
    Parameters:
    ref: reference signal
    srv: surveillance signal
    nlag: filter length in samples
    return_filter: (bool) option to return filter taps as well as cleaned signal
    
    Returns:
    y: surveillance signal with clutter removed
    w: (optional) least squares filter taps

    '''
    if ref.shape != srv.shape:
        raise ValueError('Input vectors must have the same length')
    A = np.zeros((ref.shape[0], nlag+10), dtype=np.complex64)
    lags = np.arange(-10, nlag)

    # create the data matrix
    for k in range(lags.shape[0]):
        A[:, k] = np.roll(ref, lags[k])
    
    # obtain the singular value decomposition
    U, S, V = np.linalg.svd(A, full_matrices=False)

    # zero out small singular values
    eps = 1e-10
    Sinv = 1/S
    Sinv[S < eps] = 0

    # compute the filter coefficients 
    w = V.conj().T @ np.diag(Sinv) @ U.conj().T @ srv

    if return_filter:
        return srv - A @ w, w
    else:
        return srv - A @ w

def LS_Filter_Toeplitz(ref, srv, nlag, return_filter=False):
    '''Least squares clutter removal for passive radar. Computes filter taps
    by assuming that the autocorrelation matrix of the reference channel signal 
    is Hermitian and Toeplitz. Faster than the direct matrix inversion method,
    but inaccurate if the assumptions are violated (i.e. if the input signal is
    not wide sense stationary.)
    
    
    Parameters:
    ref: reference signal
    srv: surveillance signal
    nlag: filter length in samples
    return_filter: (bool) option to return filter taps as well as cleaned signal
    
    Returns:
    y: surveillance signal with clutter removed
    w: (optional) least squares filter taps

    '''
    rs = np.roll(ref, -10)

    # compute the first column of the autocorelation matrix of ref
    c = xcorr(rs, rs, 0, nlag)

    # compute the cross-correlation of ref and srv
    r = xcorr(srv, rs, 0, nlag)

    # solve the Toeplitz least squares problem
    w = solve_toeplitz(c, r)

    if return_filter:
        return srv - np.convolve(rs, w, mode = 'same'), w
    else:
        return srv - np.convolve(rs, w, mode = 'same')


def offset_compensation(x1, x2, ndec, nlag=2000):
    s1 = x1[0:1000000]
    s2 = x2[0:1000000]
    os = find_channel_offset(s1, s2, ndec, nlag)
    if(os == 0):
        return x2
    else:
        return shift(x2, os)

def find_channel_offset(s1, s2, nd=32, nl=100):
    '''use cross-correlation to find channel offset in samples'''
    B1 = signal.decimate(s1, nd)
    B2 = np.pad(signal.decimate(s2, nd), (nl, nl), 'constant')
    xc = np.abs(signal.correlate(B1, B2, mode='valid'))
    return (np.argmax(xc) - nl)*nd
